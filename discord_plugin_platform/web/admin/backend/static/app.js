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

function showBanner(container, message, kind) {
  const element = typeof container === "string" ? document.getElementById(container) : container;
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = "banner " + (kind || "error");
  element.classList.remove("hidden");
  if (kind === "success") {
    setTimeout(() => element.classList.add("hidden"), 2500);
  }
}

function showError(container, error) {
  showBanner(container, "操作失敗：" + error.message, "error");
}

function showSuccess(container, message) {
  showBanner(container, message, "success");
}

function clearBanner(container) {
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

function statusBadge(status) {
  return el("span", { class: "badge status-" + status, text: status });
}

function getSelectedParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

function setSelectedParam(name, value) {
  const url = new URL(window.location.href);
  if (value === null || value === undefined || value === "") {
    url.searchParams.delete(name);
  } else {
    url.searchParams.set(name, value);
  }
  window.history.pushState({}, "", url);
}
