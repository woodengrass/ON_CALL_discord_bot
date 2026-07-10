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

/* ---------- 錯誤橫幅（持續顯示直到下次操作） ---------- */

function showError(container, error) {
  const element = typeof container === "string" ? document.getElementById(container) : container;
  if (!element) {
    return;
  }
  element.textContent = "操作失敗：" + error.message;
  element.className = "banner error";
  element.classList.remove("hidden");
}

function clearBanner(container) {
  const element = typeof container === "string" ? document.getElementById(container) : container;
  if (!element) {
    return;
  }
  element.textContent = "";
  element.classList.add("hidden");
}

/* ---------- Toast（成功提示，右上角短暫顯示後自動消失） ---------- */

function getToastStack() {
  let stack = document.getElementById("toast-stack");
  if (!stack) {
    stack = el("div", { id: "toast-stack", class: "toast-stack" });
    document.body.appendChild(stack);
  }
  return stack;
}

function showSuccess(_container, message) {
  const stack = getToastStack();
  const toast = el("div", { class: "toast", text: message });
  stack.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("visible"));
  setTimeout(() => {
    toast.classList.remove("visible");
    setTimeout(() => toast.remove(), 250);
  }, 2600);
}

/* ---------- 確認 / 輸入對話框，取代原生 confirm()/prompt() ---------- */

function openDialog(bodyNode, buttons) {
  return new Promise((resolve) => {
    const overlay = el("div", { class: "dialog-overlay" });
    const closeWith = (value) => {
      overlay.classList.remove("visible");
      setTimeout(() => overlay.remove(), 150);
      resolve(value);
    };
    const buttonNodes = buttons.map((button) =>
      el("button", { class: button.className || "", onclick: () => closeWith(button.value) }, [
        document.createTextNode(button.label),
      ])
    );
    const dialog = el("div", { class: "dialog" }, [bodyNode, el("div", { class: "dialog-actions" }, buttonNodes)]);
    overlay.appendChild(dialog);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        closeWith(null);
      }
    });
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add("visible"));
  });
}

function confirmDialog(message, options) {
  const opts = options || {};
  const body = el("p", { class: "dialog-message", text: message });
  return openDialog(body, [
    { label: opts.cancelLabel || "取消", value: false },
    { label: opts.confirmLabel || "確定", value: true, className: opts.danger ? "danger primary" : "primary" },
  ]);
}

/* 輸入對話框需要在按下確定時讀取輸入框當下的值，openDialog 的按鈕值是建立當下就固定的靜態值，
   沒辦法描述「讀取某個 DOM 節點目前的值」，所以這裡不重用 openDialog，直接客製一份。 */
function promptText(message, placeholder) {
  return new Promise((resolve) => {
    const overlay = el("div", { class: "dialog-overlay" });
    const input = el("input", { type: "text", placeholder: placeholder || "", class: "dialog-input" });
    const closeWith = (value) => {
      overlay.classList.remove("visible");
      setTimeout(() => overlay.remove(), 150);
      resolve(value);
    };
    const dialog = el("div", { class: "dialog" }, [
      el("p", { class: "dialog-message", text: message }),
      input,
      el("div", { class: "dialog-actions" }, [
        el("button", { text: "取消", onclick: () => closeWith(null) }),
        el("button", { text: "確定", class: "primary", onclick: () => closeWith(input.value) }),
      ]),
    ]);
    overlay.appendChild(dialog);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        closeWith(null);
      }
    });
    document.body.appendChild(overlay);
    requestAnimationFrame(() => {
      overlay.classList.add("visible");
      input.focus();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        closeWith(input.value);
      }
    });
  });
}

/* ---------- 局部載入指示 ---------- */

function withLoading(container, loadingText, task) {
  const element = typeof container === "string" ? document.getElementById(container) : container;
  if (element) {
    element.innerHTML = "";
    element.appendChild(el("div", { class: "spinner-row" }, [
      el("span", { class: "spinner" }),
      el("span", { text: loadingText || "載入中..." }),
    ]));
  }
  return task();
}

function debounce(fn, delayMs) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delayMs);
  };
}
