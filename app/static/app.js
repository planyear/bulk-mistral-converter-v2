(() => {
  const drop = document.getElementById("drop");
  const fileInput = document.getElementById("file-input");
  const picked = document.getElementById("picked");
  const pickedList = document.getElementById("picked-list");
  const submitBtn = document.getElementById("submit");
  const statusBox = document.getElementById("status");
  const statusLabel = document.getElementById("status-label");
  const resultsBody = document.querySelector("#results tbody");
  const errorEl = document.getElementById("error");
  const downloadBtn = document.getElementById("download");

  let queued = [];

  function renderPicked() {
    pickedList.innerHTML = "";
    queued.forEach((f) => {
      const li = document.createElement("li");
      li.textContent = `${f.name} (${f.size} bytes)`;
      pickedList.appendChild(li);
    });
    picked.hidden = queued.length === 0;
  }

  function addFiles(list) {
    for (const f of list) queued.push(f);
    renderPicked();
  }

  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    addFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener("change", () => addFiles(fileInput.files));

  submitBtn.addEventListener("click", async () => {
    if (!queued.length) return;
    submitBtn.disabled = true;
    const fd = new FormData();
    for (const f of queued) fd.append("files", f, f.name);
    let resp;
    try {
      resp = await fetch("/upload", { method: "POST", body: fd });
    } catch (e) {
      errorEl.textContent = `Network error: ${e}`;
      errorEl.hidden = false;
      submitBtn.disabled = false;
      return;
    }
    if (!resp.ok) {
      const body = await resp.text();
      errorEl.textContent = `Upload failed (${resp.status}): ${body}`;
      errorEl.hidden = false;
      submitBtn.disabled = false;
      return;
    }
    const { job_id } = await resp.json();
    statusBox.hidden = false;
    poll(job_id);
  });

  async function poll(jobId) {
    while (true) {
      let r;
      try {
        r = await fetch(`/jobs/${jobId}`);
      } catch (e) {
        await sleep(2000);
        continue;
      }
      if (!r.ok) {
        errorEl.textContent = `Job poll failed (${r.status})`;
        errorEl.hidden = false;
        return;
      }
      const job = await r.json();
      statusLabel.textContent = job.status;
      renderResults(job.results || []);
      if (job.error) {
        errorEl.textContent = job.error;
        errorEl.hidden = false;
      }
      if (job.status === "completed") {
        downloadBtn.href = job.download_url;
        downloadBtn.hidden = false;
        return;
      }
      if (job.status === "failed") return;
      await sleep(2000);
    }
  }

  function renderResults(results) {
    resultsBody.innerHTML = "";
    for (const r of results) {
      const tr = document.createElement("tr");
      tr.className = `row-${r.status}`;
      const detail = r.output_path || r.reason || "";
      tr.innerHTML = `<td>${esc(r.path)}</td><td>${esc(r.kind)}</td><td>${esc(r.status)}</td><td>${esc(detail)}</td>`;
      resultsBody.appendChild(tr);
    }
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function sleep(ms) {
    return new Promise((res) => setTimeout(res, ms));
  }
})();
