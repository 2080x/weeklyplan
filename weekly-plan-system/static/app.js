function filterSubProjects(categorySelect, subSelect) {
  const catId = categorySelect.value;
  const options = Array.from(subSelect.querySelectorAll("option"));
  options.forEach((opt) => {
    if (!opt.value) return;
    const optCat = opt.getAttribute("data-cat");
    opt.hidden = catId && optCat !== catId;
  });
  if (subSelect.selectedOptions.length && subSelect.selectedOptions[0].hidden) {
    subSelect.value = "";
  }
}

function wireCategoryCascade() {
  document.querySelectorAll("select[data-sub-select]").forEach((catSel) => {
    const subId = catSel.getAttribute("data-sub-select");
    const subSel = document.getElementById(subId);
    if (!subSel) return;
    const run = () => filterSubProjects(catSel, subSel);
    catSel.addEventListener("change", run);
    run();
  });
}

function wireProgressMode() {
  document.querySelectorAll(".progress-mode").forEach((modeSel) => {
    const sync = () => {
      const targetId = modeSel.getAttribute("data-target");
      const input = document.getElementById(targetId);
      if (!input) return;
      if (modeSel.value === "percent") {
        input.name = "progress_percent";
        input.value = input.value === "/" ? "" : input.value;
        input.type = "number";
        input.min = "0";
        input.max = "100";
      } else {
        input.name = "progress_text";
        if (!input.value) input.value = "/";
        input.type = "text";
        input.removeAttribute("min");
        input.removeAttribute("max");
      }
    };
    modeSel.addEventListener("change", sync);
    sync();
  });
}

function findOptionById(listEl, id) {
  if (!listEl || !id) return null;
  const options = Array.from(listEl.querySelectorAll("option"));
  return options.find((opt) => String(opt.dataset.id || "") === String(id));
}

function findOptionByName(listEl, name, catId) {
  if (!listEl || !name) return null;
  const trimmed = name.trim();
  if (!trimmed) return null;
  const options = Array.from(listEl.querySelectorAll("option"));
  return options.find((opt) => {
    if (opt.value !== trimmed) return false;
    if (!catId) return true;
    return String(opt.dataset.cat || "") === String(catId);
  });
}

function cloneOption(opt) {
  const cloned = document.createElement("option");
  cloned.value = opt.value;
  if (opt.dataset.id) cloned.dataset.id = opt.dataset.id;
  if (opt.dataset.cat) cloned.dataset.cat = opt.dataset.cat;
  return cloned;
}

function rebuildSubList(subListId, catId) {
  const target = document.getElementById(subListId);
  const source = document.getElementById("subprojects-source");
  if (!target || !source) return;
  target.innerHTML = "";
  Array.from(source.querySelectorAll("option")).forEach((opt) => {
    if (catId && String(opt.dataset.cat || "") !== String(catId)) return;
    target.appendChild(cloneOption(opt));
  });
}

async function createCategory(name) {
  const resp = await fetch("/api/dicts/category", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.id) {
    throw new Error(data.message || data.detail || "保存失败");
  }
  return data;
}

async function createSubProject(name, categoryId) {
  const resp = await fetch("/api/dicts/subproject", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, category_id: Number(categoryId) }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.id) {
    throw new Error(data.message || data.detail || "保存失败");
  }
  return data;
}

function wireDictInputs() {
  const catSource = document.getElementById("categories-source");
  const subSource = document.getElementById("subprojects-source");
  if (!catSource || !subSource) return;

  document.querySelectorAll("[data-dict-input='category']").forEach((input) => {
    const wrap = input.closest("[data-dict-wrap]");
    const hidden = wrap?.querySelector("input[name='category_id']");
    const subListId = input.getAttribute("data-sub-list");
    const subInputId = input.getAttribute("data-sub-input");

    const fillFromHidden = () => {
      if (hidden?.value && !input.value) {
        const opt = findOptionById(catSource, hidden.value);
        if (opt) input.value = opt.value;
      }
    };

    const sync = () => {
      const raw = input.value.trim();
      const matched = findOptionByName(catSource, raw);

      let catId = hidden?.value || "";
      if (!raw) {
        catId = "";
        if (hidden) hidden.value = "";
      } else if (matched?.dataset.id) {
        catId = matched.dataset.id;
        if (hidden) hidden.value = catId;
      } else if (catId) {
        const opt = findOptionById(catSource, catId);
        if (opt) input.value = opt.value;
      }

      if (subListId) rebuildSubList(subListId, catId);
      const subInput = document.getElementById(subInputId);
      const subHidden = subInput?.closest("[data-dict-wrap]")?.querySelector("input[name='sub_project_id']");
      if (subInput && subHidden) {
        const subMatch = subHidden.value ? findOptionById(subSource, subHidden.value) : null;
        if (!subMatch || (catId && String(subMatch.dataset.cat || "") !== String(catId))) {
          subHidden.value = "";
          subInput.value = "";
        }
      }
    };

    fillFromHidden();
    sync();
    input.addEventListener("change", sync);
    input.addEventListener("blur", sync);
  });

  document.querySelectorAll("[data-dict-input='subproject']").forEach((input) => {
    const wrap = input.closest("[data-dict-wrap]");
    const hidden = wrap?.querySelector("input[name='sub_project_id']");
    const catInputId = input.getAttribute("data-cat-input");
    const subListId = input.getAttribute("data-sub-list");

    const getCatId = () => {
      const catInput = document.getElementById(catInputId);
      const catHidden = catInput?.closest("[data-dict-wrap]")?.querySelector("input[name='category_id']");
      return catHidden?.value || "";
    };

    const fillFromHidden = () => {
      if (hidden?.value && !input.value) {
        const opt = findOptionById(subSource, hidden.value);
        if (opt) input.value = opt.value;
      }
    };

    const sync = () => {
      const catId = getCatId();
      if (subListId) rebuildSubList(subListId, catId || null);
      const listEl = document.getElementById(subListId);
      const raw = input.value.trim();
      const matched = findOptionByName(listEl, raw, catId || null);

      if (!raw) {
        if (hidden) hidden.value = "";
        return;
      }
      if (matched?.dataset.id) {
        if (hidden) hidden.value = matched.dataset.id;
        return;
      }
      const keepId = hidden?.value || "";
      if (keepId) {
        const opt = findOptionById(subSource, keepId);
        if (opt) input.value = opt.value;
      }
    };

    fillFromHidden();
    sync();
    input.addEventListener("change", sync);
    input.addEventListener("blur", sync);
  });
}

let planDialogRefs = null;
function getPlanDialogRefs() {
  if (planDialogRefs !== null) return planDialogRefs;
  const dialog = document.getElementById("planDialog");
  const frame = document.getElementById("planDialogFrame");
  const closeBtn = dialog?.querySelector("[data-plan-close]");
  const refreshBtn = dialog?.querySelector("[data-plan-refresh]");
  planDialogRefs = { dialog, frame, closeBtn, refreshBtn };
  return planDialogRefs;
}

function openPlanDialog(url) {
  const { dialog, frame } = getPlanDialogRefs();
  if (dialog && frame) {
    frame.src = url;
    dialog.showModal();
    return false;
  }
  // 兜底：如果找不到弹窗元素，回退为新窗口
  window.open(url, "_blank", "noopener,noreferrer");
  return false;
}
window.openPlanDialog = openPlanDialog;

function wireMonthAutoSubmit() {
  document.querySelectorAll("form[data-auto-submit] input[type='month'][name='ym']").forEach((input) => {
    const form = input.closest("form");
    if (!form) return;
    input.addEventListener("change", () => form.submit());
  });
}

function wireRefreshButtons() {
  document.querySelectorAll("[data-refresh]").forEach((btn) => {
    btn.addEventListener("click", () => window.location.reload());
  });
}

function wireSendEmailButtons() {
  document.querySelectorAll("[data-send-email]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const planId = btn.getAttribute("data-send-email");
      if (!planId) return;
      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "发送中...";
      try {
        const resp = await fetch(`/plans/${planId}/send-email`, { method: "POST" });
        const data = await resp.json();
        if (!data.ok) {
          alert(data.message || "发送失败");
        } else {
          alert("邮件已发送");
        }
      } catch (e) {
        alert("发送失败");
      } finally {
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });
  });
}

function wirePlanPopupWindow() {
  const { dialog, frame, closeBtn, refreshBtn } = getPlanDialogRefs();
  if (!dialog || !frame) return;

  closeBtn?.addEventListener("click", () => {
    dialog.close();
  });
  refreshBtn?.addEventListener("click", () => {
    if (frame.contentWindow) {
      frame.contentWindow.location.reload();
    }
  });

  dialog.addEventListener("close", () => {
    frame.src = "";
  });

  document.querySelectorAll("a[data-open-plan-window]").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const href = a.getAttribute("href");
      if (!href) return;
      openPlanDialog(href);
    });
  });
}

function wireAddItemDialog() {
  const dlg = document.getElementById("addItemDialog");
  if (!dlg) return;
  const openBtn = document.querySelector("[data-open-add-item]");
  const closeBtns = dlg.querySelectorAll("[data-close-add-item]");

  openBtn?.addEventListener("click", () => dlg.showModal());
  closeBtns.forEach((btn) => {
    btn.addEventListener("click", () => dlg.close());
  });
}

function wirePlanRowEditToggle() {
  const buttons = document.querySelectorAll("[data-toggle-edit]");
  if (!buttons.length) return;

  const setEditing = (row, editing) => {
    row.classList.toggle("is-editing", editing);
    const btn = row.querySelector("[data-toggle-edit]");
    if (btn) btn.textContent = editing ? "保存" : "修改";
  };

  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest("tr");
      const form = btn.closest("form");
      if (!row || !form) return;

      const isEditing = row.classList.contains("is-editing");
      if (!isEditing) {
        document.querySelectorAll("tr.is-editing").forEach((other) => {
          if (other !== row) setEditing(other, false);
        });
        setEditing(row, true);
        const first = row.querySelector(".cell-edit input, .cell-edit textarea, .cell-edit select");
        first?.focus();
        return;
      }

      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.submit();
      }
    });
  });
}

window.addEventListener("DOMContentLoaded", () => {
  wireCategoryCascade();
  wireProgressMode();
  wireMonthAutoSubmit();
  wireRefreshButtons();
  wirePlanPopupWindow();
  wireSendEmailButtons();
  wireAddItemDialog();
  wireDictInputs();
  wirePlanRowEditToggle();
});
