"""
獨立執行入口，串接 engine.py + capability_bindings.py，執行單次事件分派。
見 design.md 第 3.2 節元件規格與第 6.4 節資料夾結構說明。
"""

import asyncio
import logging
import multiprocessing
from multiprocessing.connection import Connection

import aiosqlite

from core.bot_registry import get_bot
from core.capability_api import ExecutionContext, InProcessBackend, RpcBackend
from sandbox.capability_bindings import bind_capabilities
from sandbox.engine import SandboxExecutionError, create_sandbox_runtime, execute_untrusted_code, run_with_limits
from sandbox.rpc_server import serve_capability_requests

logger = logging.getLogger(__name__)

EXECUTION_TIMEOUT_SECONDS = 2.0
PROCESS_TERMINATE_GRACE_SECONDS = 1.0


async def execute_plugin_event(
    guild_id: int,
    plugin_id: str,
    source_code: str,
    event_type: str,
    event_payload: dict,
    granted_capabilities: set[str],
    execution_db: aiosqlite.Connection | None = None,
    resource_overrides: dict | None = None,
) -> list[dict]:
    """
    在全新子行程執行單次外掛事件，主行程只負責服務能力 RPC 與回收行程。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        source_code: 外掛的 Lua 原始碼
        event_type: 觸發的事件名稱，對應 Lua 裡的同名函式（例如 on_message）
        event_payload: 事件資料
        granted_capabilities: 這次安裝授權的能力旗標集合
        execution_db: 這次執行專用的資料庫連線，留在主行程端的 InProcessBackend 使用，
            不會傳進子行程，避免繞開 dispatcher 的 commit/rollback 邊界。
        resource_overrides: 已解析的資源限制，分別提供給子行程沙箱限制與主行程 storage 限制。

    Returns:
        動作清單，格式見 design.md 第 3.2 節「動作清單格式」

    Raises:
        SandboxExecutionError: 外掛原始碼載入失敗、執行逾時、超過資源限制，或子行程異常終止
    """
    process_context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = process_context.Pipe(duplex=True)
    process = process_context.Process(
        target=_child_process_main,
        args=(
            child_connection,
            guild_id,
            plugin_id,
            source_code,
            event_type,
            event_payload,
            granted_capabilities,
            resource_overrides,
        ),
    )
    process.start()
    child_connection.close()

    backend = InProcessBackend(
        guild_id=guild_id,
        plugin_id=plugin_id,
        bot=get_bot(),
        event_loop=asyncio.get_running_loop(),
        execution_db=execution_db,
        resource_overrides=resource_overrides,
    )
    rpc_task = asyncio.create_task(serve_capability_requests(parent_connection, backend))

    try:
        done_message = await asyncio.wait_for(rpc_task, timeout=EXECUTION_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as error:
        await _terminate_process(process)
        rpc_task.cancel()
        raise SandboxExecutionError("外掛子行程執行逾時") from error
    except Exception as error:
        await _join_process(process)
        raise SandboxExecutionError(f"外掛子行程未正常回傳結果：{error}") from error
    finally:
        parent_connection.close()

    await _join_process(process)
    if process.exitcode not in (0, None):
        raise SandboxExecutionError(f"外掛子行程異常終止，結束碼 {process.exitcode}")

    error_payload = done_message.get("error")
    if error_payload is not None:
        raise SandboxExecutionError(error_payload.get("message", "外掛子行程執行失敗"))
    return done_message.get("action_queue", [])


def _child_process_main(
    connection: Connection,
    guild_id: int,
    plugin_id: str,
    source_code: str,
    event_type: str,
    event_payload: dict,
    granted_capabilities: set[str],
    resource_overrides: dict | None,
) -> None:
    """
    子行程進入點：建立 Lua VM、綁定 RpcBackend、執行外掛並回傳最終結果。

    Args:
        connection: 子行程端 pipe connection
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        source_code: 外掛 Lua 原始碼
        event_type: 要呼叫的事件處理函式名稱
        event_payload: 事件資料
        granted_capabilities: 已授權能力集合
        resource_overrides: 已解析的資源限制
    """
    try:
        context = ExecutionContext(
            guild_id=guild_id,
            plugin_id=plugin_id,
            granted_capabilities=granted_capabilities,
            backend=RpcBackend(connection),
        )
        runtime = create_sandbox_runtime(
            instruction_limit=(resource_overrides or {}).get("instruction_limit"),
            memory_limit_bytes=(resource_overrides or {}).get("memory_limit_bytes"),
        )
        bind_capabilities(runtime, context)
        execute_untrusted_code(runtime, source_code)
        run_with_limits(runtime, event_type, event_payload)
        connection.send({"kind": "done", "action_queue": context.action_queue})
    except Exception as error:
        connection.send(
            {
                "kind": "done",
                "error": {"type": "SandboxExecutionError", "message": str(error)},
            }
        )
    finally:
        connection.close()


async def _terminate_process(process: multiprocessing.Process) -> None:
    """
    終止逾時子行程，先 terminate，仍存活則 kill，最後一定 join。

    Args:
        process: 要清理的子行程
    """
    if process.is_alive():
        process.terminate()
        await _join_process(process, PROCESS_TERMINATE_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
    await _join_process(process)


async def _join_process(process: multiprocessing.Process, timeout: float | None = None) -> None:
    """
    在背景執行緒等待 process.join()，避免阻塞 asyncio event loop。

    Args:
        process: 要等待的子行程
        timeout: join timeout，None 表示等到結束
    """
    await asyncio.to_thread(process.join, timeout)
