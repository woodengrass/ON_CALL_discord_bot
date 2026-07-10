"""
FastAPI 依賴注入，見 design.md H.1：v1 綁定 127.0.0.1、不做真正登入驗證，
但所有需要授權的路由都宣告依賴 get_current_operator()，之後真的要加白名單登入時
只需要改這個函式的內部實作，不用逐一修改路由。
"""


def get_current_operator() -> str:
    """
    取得目前操作者識別。

    Returns:
        操作者識別字串，v1 固定回傳本機操作者
    """
    return "local-operator"
