const API_BASE = "https://9239klfkci.execute-api.eu-central-1.amazonaws.com/Prod";

const els = {
  file: document.getElementById("file"),
  status: document.getElementById("status"),
  q: document.getElementById("q"),
  askBtn: document.getElementById("askBtn"),
  answer: document.getElementById("answer"),
  sources: document.getElementById("sources"),
};
let lastDocId = null;

function setStatus(msg) { els.status.textContent = msg; }
function setAnswer(msg) { els.answer.textContent = msg; }
function clearSources() { els.sources.innerHTML = ""; }
function addSource(page, url) {
  const a = document.createElement("a");
  a.href = url; a.target = "_blank"; a.textContent = "Page " + page;
  els.sources.appendChild(a);
}

async function uploadNote(file) {
  const fd = new FormData();
  fd.append("file", file);
  els.file.disabled = true;
  setStatus("Uploading…");
  try {
    const res = await fetch(API_BASE + "/upload", { 
      method: "POST", 
      body: fd,
      mode: 'cors',  // Add CORS mode
      headers: {
        'Accept': 'application/json',
      }
    });
    
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.detail || `Upload failed: ${res.status}`);
    }
    
    const data = await res.json();
    lastDocId = data.doc_id;
    setStatus(`Uploaded. Starting OCR processing...`);
    
    // Wait a few seconds for Textract to start
    await new Promise(resolve => setTimeout(resolve, 6000));
    
    // Process the document
    const processRes = await fetch(`${API_BASE}/process?doc_id=${encodeURIComponent(lastDocId)}`, {
      method: "POST",
      mode: 'cors',
      headers: {
        'Accept': 'application/json',
      }
    });
    
    if (!processRes.ok) {
      throw new Error("Processing failed: " + processRes.status);
    }
    
    const processData = await processRes.json();
    if (processData.ok) {
      setStatus(`✅ Successfully processed ${processData.pages} pages. Ready to ask questions!`);
    } else {
      throw new Error(processData.msg || "Processing failed");
    }
  } catch (err) {
    setStatus("❌ " + err.message);
    console.error("Error:", err);
  } finally {
    els.file.disabled = false;
  }
}

function ensureProcessBtn() {
  if (document.getElementById("processBtn")) return;
  const btn = document.createElement("button");
  btn.id = "processBtn";
  btn.textContent = "Process OCR Results";
  btn.style.marginLeft = "8px";
  btn.onclick = processDoc;
  els.status.parentElement.appendChild(btn);
}

async function processDoc() {
  if (!lastDocId) return setStatus("No doc_id yet. Upload first.");
  setStatus("Processing OCR results…");
  try {
    const res = await fetch(`${API_BASE}/process?doc_id=${encodeURIComponent(lastDocId)}`, { method: "POST" });
    const text = await res.text();
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(`(${res.status}) ${data.msg || text}`);
    setStatus(`✅ Processed ${data.pages} page(s). Ready to ask.`);
  } catch (e) {
    setStatus("❌ " + e.message);
  }
}

async function ask() {
  const q = els.q.value.trim();
  if (!q) return;
  els.askBtn.disabled = true;
  setAnswer("Thinking…");
  clearSources();
  try {
    const res = await fetch(API_BASE + "/ask?q=" + encodeURIComponent(q));
    const data = await res.json();
    setAnswer(data.answer || "(no answer)");
    (data.sources || []).forEach(s => addSource(s.page, s.url));
  } catch (e) {
    setAnswer("❌ " + e.message);
  } finally {
    els.askBtn.disabled = false;
  }
}

els.file.addEventListener("change", (e) => {
  if (e.target.files[0]) uploadNote(e.target.files[0]);
});
els.askBtn.addEventListener("click", ask);