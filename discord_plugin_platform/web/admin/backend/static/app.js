/*
共用 API 呼叫與畫面小工具，見 design.md H.1：前端只是呼叫 /api/... 的薄層，
所有資料都在瀏覽器端用 fetch() 拿，不做 SPA 框架、不做建置流程。
*/

async function apiRequest(method, path, body) {
  const response = await fetch(path, {
    method: method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return data;
}

function apiGet(path) {
  return apiRequest("GET", path);
}

function apiPost(path, body) {
  return apiRequest("POST", path, body || {});
}

function apiPut(path, body) {
  return apiRequest("PUT", path, body || {});
}

function apiDelete(path) {
  return apiRequest("DELETE", path);
}

function showError(container, error) {
  const element = typeof container === "string" ? document.getElementById(container) : container;
  if (!element) {
    return;
  }
  element.textContent = "操作失敗：" + error.message;
  element.classList.remove("hidden");
}

function clearError(container) {
  const element = typeof container === "string" ? document.getElementById(container) : container;
  if (!element) {
    return;
  }
  element.textContent = "";
  element.classList.add("hidden");
}

function el(tag, attributes, children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes || {})) {
    if (key === "text") {
      node.textContent = value;
    } else if (key.startsWith("on")) {
      node.addEventListener(key.slice(2), value);
    } else {
      node.setAttribute(key, value);
    }
  }
  for (const child of children || []) {
    node.appendChild(child);
  }
  return node;
}
