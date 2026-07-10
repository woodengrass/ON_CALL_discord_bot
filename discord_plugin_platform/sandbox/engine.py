"""
純沙箱機制：建立乾淨、有資源限制的空 Lua VM，完全不認識 Discord 或能力 API 是什麼。
見 discord_plugin_platform/design.md 第 5.3、5.4 節。
"""

import logging

import lupa.lua54 as lua54

logger = logging.getLogger(__name__)

INSTRUCTION_LIMIT = 500_000
MEMORY_LIMIT_BYTES = 16 * 1024 * 1024
MEMORY_LIMIT_KB = MEMORY_LIMIT_BYTES // 1024
HOOK_CHECK_INTERVAL = 1000  # 每執行這麼多條指令，才觸發一次步數/記憶體檢查

# string.rep(s, n) 單一呼叫就能配置任意大小的記憶體，且整個呼叫在 Lua VM 眼中只算
#「一條指令」——debug.sethook 的計數鉤子只在指令與指令「之間」才有機會被呼叫，
# C 函式執行期間完全不會被打斷，所以像 string.rep("x", 1024^3) 這種單一陳述式、
# 後面沒有其他程式碼的腳本，鉤子可能永遠沒有機會在配置完成前後被觸發，導致 16MB
# 的記憶體軟上限完全沒用。這裡直接把單次可配置的長度上限設成跟整體記憶體上限一樣，
# 見 create_sandbox_runtime() 裡 _cap_dangerous_string_functions() 的說明。
MAX_SINGLE_STRING_ALLOCATION_BYTES = MEMORY_LIMIT_BYTES

# 只保留這些安全的基礎函式庫，其餘全域函式一律清空。
# 刻意不放行 coroutine：debug.sethook 安裝的鉤子預設只作用在主執行緒，
# 外掛若自己開一個 coroutine 在裡面跑無窮迴圈，會完全繞過步數限制鉤子，
# 這是設計階段沒發現、寫測試時才找到的第 6 種逃逸手法，見 design.md 第 5.3 節。
SANDBOX_GLOBAL_ALLOWLIST = {
    "pairs", "ipairs", "next", "type", "tostring", "tonumber",
    "table", "string", "math", "select", "unpack",
    "rawequal", "rawget", "rawset", "rawlen",
    "error", "assert",
}
# 刻意不放行 pcall／xpcall：步數/記憶體上限鉤子是靠呼叫 error() 中止執行，
# 如果外掛能用 pcall 包住自己的無窮迴圈，鉤子丟出的 error 會被 pcall 接住、
# 外掛可以立刻重新進入下一輪迴圈，等於完全繞過步數上限，實測會讓執行永遠不停。
# 這是第 7 種逃逸手法，設計階段沒發現，寫沙箱程式碼時才實測抓到，見 design.md 第 5.3 節。


class SandboxExecutionError(Exception):
    """
    沙箱執行失敗時拋出（逾時、超過資源限制、Lua 執行期例外等）。
    """


def create_sandbox_runtime(
    instruction_limit: int | None = None,
    memory_limit_bytes: int | None = None,
) -> lua54.LuaRuntime:
    """
    建立一個乾淨的 Lua VM，移除所有危險全域函式，套用執行步數與記憶體限制。

    刻意指定 lupa.lua54（穩定版 Lua 5.4），不用 lupa.LuaRuntime 預設值：
    lupa 2.8 預設會解析到 lupa.lua55（實驗性 Lua 5.5 build），實測發現這個 build 的
    collectgarbage("count") 不會正確回報字串配置的記憶體用量（配置 47MB 的字串資料，
    count() 只增加 3.6KB），導致以字串為主的記憶體炸彈完全不會被攔截，是嚴重的安全漏洞。
    同樣的測試在 lupa.lua54 下 count() 正確回報約 48MB 增量。見 design.md 第 5.3、5.4 節。

    Returns:
        設定好白名單全域環境與資源限制的 LuaRuntime
    """
    effective_instruction_limit = instruction_limit or INSTRUCTION_LIMIT
    effective_memory_limit_bytes = memory_limit_bytes or MEMORY_LIMIT_BYTES
    runtime = lua54.LuaRuntime(
        register_eval=False,
        register_builtins=False,
        unpack_returned_tuples=True,
    )

    _install_resource_limit_hook(runtime, effective_instruction_limit, effective_memory_limit_bytes)
    _cap_dangerous_string_functions(runtime, effective_memory_limit_bytes)
    _strip_globals_to_allowlist(runtime)

    return runtime


def _cap_dangerous_string_functions(runtime: lua54.LuaRuntime, max_single_string_allocation_bytes: int) -> None:
    """
    把 string.rep 換成一個會先檢查輸出長度上限的版本，防止單一呼叫瞬間配置
    超過 MAX_SINGLE_STRING_ALLOCATION_BYTES 的記憶體。

    背景：debug.sethook 的計數鉤子只在 Lua 指令與指令「之間」才有機會被呼叫，
    C 函式（例如 string.rep）執行期間完全不會被打斷；如果外掛程式碼是
    `local bomb = string.rep("x", 1024^3)` 這種單一陳述式、後面沒有其他程式碼
    的腳本，鉤子可能永遠沒有機會在配置完成前後被觸發，導致記憶體上限完全沒用
    （design.md 第 5.3 節第 5 種逃逸手法，也是實測才發現原本的迴圈型測試沒有真的
    驗證到這個情境）。這裡直接在呼叫前用 Lua 算出結果長度，超過上限就直接 error()，
    不讓底層真正配置那塊記憶體。

    Args:
        runtime: 尚未清空全域表的 LuaRuntime（string 函式庫本身在白名單內，不會被清空）
        max_single_string_allocation_bytes: 單次 string.rep 可配置的最大 byte 數
    """
    cap_string_rep_code = f"""
    local raw_string_rep = string.rep
    string.rep = function(s, n, sep)
        local separator_length = sep and #sep or 0
        local repeat_count = n or 0
        local estimated_length = (#s + separator_length) * repeat_count
        if estimated_length > {max_single_string_allocation_bytes} then
            error("string.rep 單次配置長度超過上限（{max_single_string_allocation_bytes} bytes）")
        end
        return raw_string_rep(s, n, sep)
    end
    """
    try:
        runtime.execute(cap_string_rep_code)
    except Exception as error:
        raise SandboxExecutionError(f"套用 string.rep 上限失敗：{error}") from error


def _install_resource_limit_hook(
    runtime: lua54.LuaRuntime,
    instruction_limit: int,
    memory_limit_bytes: int,
) -> None:
    """
    安裝執行步數與記憶體限制的鉤子，必須在 _strip_globals_to_allowlist() 之前呼叫，
    因為這一步需要用到 debug 函式庫本身；鉤子一旦透過 debug.sethook 安裝成功，
    之後把 debug 從全域表移除也不影響鉤子繼續運作（鉤子是直譯器層級的機制，
    不是透過 Lua 程式碼持續呼叫 debug 表才生效）。

    Args:
        runtime: 尚未清空全域表的 LuaRuntime
        instruction_limit: 執行步數上限
        memory_limit_bytes: 記憶體上限（bytes）

    Raises:
        SandboxExecutionError: 鉤子安裝失敗（不應該發生，除非 lupa／LuaJIT 版本不支援 debug.sethook）
    """
    memory_limit_kb = memory_limit_bytes // 1024
    max_hook_calls = instruction_limit // HOOK_CHECK_INTERVAL
    install_hook_code = f"""
    -- 先把 collectgarbage 存成 local 變數（鉤子閉包的 upvalue），
    -- 之後清空全域表時會把 collectgarbage 從 _G 移除，鉤子本身仍能透過這個
    -- local 參照繼續呼叫；這同時也是刻意的安全設計：外掛程式碼本身不該能直接
    -- 呼叫 collectgarbage（例如 collectgarbage("stop") 會讓外掛自己關掉 GC，
    -- 等於繞過記憶體上限檢查），清空之後外掛就完全拿不到這個函式了。
    local collectgarbage_ref = collectgarbage
    local hook_calls = 0
    debug.sethook(function()
        hook_calls = hook_calls + 1
        if hook_calls > {max_hook_calls} then
            error("執行步數超過上限（{instruction_limit} 步）")
        end
        if collectgarbage_ref("count") > {memory_limit_kb} then
            error("記憶體用量超過上限（{memory_limit_kb}KB）")
        end
    end, "", {HOOK_CHECK_INTERVAL})
    """
    try:
        runtime.execute(install_hook_code)
    except Exception as error:
        raise SandboxExecutionError(f"安裝資源限制鉤子失敗：{error}") from error


def _strip_globals_to_allowlist(runtime: lua54.LuaRuntime) -> None:
    """
    把不在 SANDBOX_GLOBAL_ALLOWLIST 裡的全域函式全部清空，只留下安全的基礎函式庫。
    必須在 _install_resource_limit_hook() 之後呼叫。

    Args:
        runtime: 已經裝好資源限制鉤子的 LuaRuntime
    """
    globals_table = runtime.globals()
    keys_to_remove = [key for key in globals_table.keys() if key not in SANDBOX_GLOBAL_ALLOWLIST]
    for key in keys_to_remove:
        globals_table[key] = None


def execute_untrusted_code(runtime: lua54.LuaRuntime, lua_code: str) -> None:
    """
    在沙箱裡執行一段 Lua 原始碼，資源限制鉤子若觸發會讓這裡拋出例外。

    Args:
        runtime: create_sandbox_runtime() 建立的 LuaRuntime
        lua_code: 要執行的 Lua 原始碼

    Raises:
        SandboxExecutionError: 執行期間超過步數/記憶體上限，或程式碼本身丟出未捕捉例外
    """
    try:
        runtime.execute(lua_code)
    except Exception as error:
        raise SandboxExecutionError(str(error)) from error


def run_with_limits(runtime: lua54.LuaRuntime, lua_function_name: str, payload: dict) -> list[dict]:
    """
    在資源限制下執行沙箱內指定的 Lua 函式。

    Args:
        runtime: create_sandbox_runtime() 建立的 LuaRuntime，且已經由
            sandbox.capability_bindings.bind_capabilities() 綁定好能力函式與動作佇列
        lua_function_name: 要呼叫的外掛事件處理函式名稱（例如 "on_message"）
        payload: 事件資料，會轉換成 Lua table 傳入

    Returns:
        外掛執行期間排入佇列的動作清單

    Raises:
        SandboxExecutionError: 執行逾時、超過資源限制，或執行中途拋出未捕捉例外
            （中途崩潰時整批動作清單回退，不回傳任何部分結果，見 design.md 第 3.2 節）

    Note:
        這裡只靠步數／記憶體鉤子攔截失控的 Lua 程式碼；真正的行程層級逾時保護
        （例如鉤子本身因未知原因沒有觸發）留給 sandbox/worker.py 用獨立進程 + 逾時
        機制處理，這裡不重複做一層無法真正搶佔同步呼叫的 asyncio 逾時。
    """
    lua_function = runtime.globals()[lua_function_name]
    if lua_function is None:
        raise SandboxExecutionError(f"外掛沒有定義事件處理函式：{lua_function_name}")

    try:
        lua_function(runtime.table_from(payload, recursive=True))
    except Exception as error:
        raise SandboxExecutionError(str(error)) from error

    action_queue = runtime.globals()["_action_queue"]
    if action_queue is None:
        return []
    return [lua_value_to_python(action) for action in action_queue.values()]


def lua_value_to_python(value):
    """
    把 Lua table（可能巢狀）遞迴轉換成純 Python dict/list，非 table 的值原樣傳回。

    Lua 沒有原生陣列型別，array-like table（key 剛好是從 1 到 n 的連續整數，不管
    順序為何）轉成 list，其餘 table 轉成 dict；action_queue 裡的 params 常常有巢狀
    table（例如身分組 ID 陣列），只轉換最外層會讓呼叫端拿到殘留的 Lua table 物件，
    無法正常做 JSON 序列化或稽核紀錄寫入。

    判斷是不是 array-like 時看的是 key 的「集合」剛好等於 {1, ..., n}，不能只看
    `value.items()` 回傳的順序跟 [1, 2, ..., n] 是否剛好一樣——Lua table 的雜湊部分
    在某些操作後（例如先設定又刪除元素）遍歷順序不保證跟 key 大小一致，只比對順序
    會把貨真價實的 array-like table 誤判成 dict，資料本身雖然沒壞但型別不穩定，
    下游程式碼可能一下拿到 list 一下拿到 dict。

    Args:
        value: 任意 Lua 回傳值（table、字串、數字、布林、None）

    Returns:
        遞迴轉換後的純 Python 值
    """
    if lua54.lua_type(value) != "table":
        return value

    items = dict(value.items())
    is_array = set(items.keys()) == set(range(1, len(items) + 1))
    if is_array:
        return [lua_value_to_python(items[index]) for index in range(1, len(items) + 1)]
    return {key: lua_value_to_python(item_value) for key, item_value in items.items()}
