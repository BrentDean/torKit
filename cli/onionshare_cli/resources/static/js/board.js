"use strict";

// Optional UX only. No drafts or display names are stored in browser storage.
document.querySelectorAll("a[href='#new-discussion']").forEach((link) => {
  link.addEventListener("click", () => {
    const composer = document.getElementById("new-discussion");
    if (composer) composer.open = true;
  });
});

document.querySelectorAll(".post-form").forEach((form) => {
  const textarea = form.querySelector("textarea[name='body']");
  const counter = form.querySelector("[data-count-for='body']");
  const publish = form.querySelector("button[type='submit']");
  if (!textarea || !counter) return;
  const picker = form.querySelector("input[type='file'][name='attachment']");
  const preview = form.querySelector(".selected-file");
  const remove = form.querySelector(".remove-attachment");
  let localPreviewUrl = null;
  const maxBytes = 10 * 1024 * 1024;

  function updateSelection() {
    if (localPreviewUrl) URL.revokeObjectURL(localPreviewUrl);
    localPreviewUrl = null;
    if (preview) preview.replaceChildren();
    textarea.setCustomValidity("");
    if (!picker) return;
    if (remove) remove.hidden = !picker.files.length;
    picker.setCustomValidity("");
    const selected = picker.files[0];
    if (!selected) return;
    if (selected.size > maxBytes) {
      picker.setCustomValidity("Select a file of 10 MB or less.");
    }
    if (preview) {
      const name = document.createElement("span");
      name.textContent = selected.name + " · " +
        (selected.size / 1024).toFixed(1) + " KB";
      preview.append(name);
      const suffix = selected.name.split(".").pop().toLowerCase();
      if (["jpg", "jpeg", "png", "webp", "gif"].includes(suffix) &&
          selected.type.startsWith("image/") && selected.size <= maxBytes) {
        const image = document.createElement("img");
        image.alt = "Local image preview: " + selected.name;
        localPreviewUrl = URL.createObjectURL(selected);
        image.src = localPreviewUrl;
        preview.append(image);
      }
    }
    if (picker.validationMessage) picker.reportValidity();
  }

  if (picker) picker.addEventListener("change", updateSelection);
  if (picker && remove) remove.addEventListener("click", () => {
    picker.value = "";
    updateSelection();
    picker.focus();
  });
  window.addEventListener("pagehide", () => {
    if (localPreviewUrl) URL.revokeObjectURL(localPreviewUrl);
  });
  textarea.addEventListener("input", () => textarea.setCustomValidity(""));


  function updateCount() {
    counter.textContent = textarea.value.length.toLocaleString() + " / 5,000";
  }
  textarea.addEventListener("input", updateCount);
  updateCount();

  // Keyboard-friendly convenience. Native HTML validation still applies.
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      if (form.reportValidity()) form.requestSubmit();
    }
  });
  form.addEventListener("submit", (event) => {
    if (!textarea.value.trim() && !(picker && picker.files.length && picker.files[0].name)) {
      textarea.setCustomValidity("Write a message or attach a file.");
      textarea.reportValidity();
      event.preventDefault();
      return;
    }
    if (picker && !picker.checkValidity()) {
      picker.reportValidity();
      event.preventDefault();
      return;
    }
    if (publish) {
      publish.disabled = true;
      publish.textContent = "Publishing…";
    }
  });
});

const copyLink = document.querySelector("[data-copy-link]");
if (copyLink) {
  copyLink.addEventListener("click", async () => {
    const feedback = document.querySelector(".copy-feedback");
    try {
      await navigator.clipboard.writeText(window.location.href);
      if (feedback) feedback.textContent = "Link copied.";
    } catch (_) {
      if (feedback) {
        feedback.textContent = "Clipboard unavailable. Copy the address from your browser.";
      }
    }
  });
}
