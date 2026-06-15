if (window.location.protocol === "file:") {
  window.location.replace(`http://127.0.0.1:8000/demo/${window.location.hash}`);
}

document.querySelectorAll("[data-scroll]").forEach((button) => {
  button.addEventListener("click", () => document.querySelector(button.dataset.scroll)?.scrollIntoView({ behavior: "smooth" }));
});

const menuToggle = document.querySelector(".menu-toggle");
const nav = document.querySelector(".main-nav");
menuToggle.addEventListener("click", () => {
  const open = nav.classList.toggle("open");
  menuToggle.setAttribute("aria-expanded", String(open));
});
nav.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => nav.classList.remove("open")));

const observer = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) entry.target.classList.add("visible");
  });
}, { threshold: 0.12 });
document.querySelectorAll(".reveal").forEach((element) => observer.observe(element));

const sections = [...document.querySelectorAll("main section[id]")];
const navLinks = [...nav.querySelectorAll("a")];
const sectionObserver = new IntersectionObserver((entries) => {
  const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
  if (!visible) return;
  navLinks.forEach((link) => link.classList.toggle("active", link.getAttribute("href") === `#${visible.target.id}`));
}, { rootMargin: "-25% 0px -60%", threshold: [0.05, 0.25, 0.5] });
sections.forEach((section) => sectionObserver.observe(section));

const topicInput = document.querySelector("#topic");
const runButton = document.querySelector("#run-pipeline");
const runItems = [...document.querySelectorAll("[data-run-step]")];
const runnerStatus = document.querySelector("#runner-status");
const progressBar = document.querySelector("#run-progress-bar");
const result = document.querySelector("#run-result");
const runPreviewButton = document.querySelector("#open-run-preview");
let statusTimer = null;

document.querySelectorAll("[data-topic]").forEach((button) => {
  button.addEventListener("click", () => {
    topicInput.value = button.dataset.topic;
    topicInput.focus();
  });
});

function resetRunner() {
  clearTimeout(statusTimer);
  runItems.forEach((item) => {
    item.className = "";
    item.querySelector("b").textContent = "待命";
  });
  progressBar.style.width = "0";
  result.classList.remove("show");
}

function updateResult(data) {
  const image = result.querySelector("img");
  image.src = data.cover_url;
  image.alt = `${data.title}封面`;
  result.querySelector("strong").textContent = data.title;
  runPreviewButton.onclick = () => window.open(data.preview_url, "_blank", "noopener");
}

function buildResultCard(run) {
  const card = document.createElement("article");
  card.className = "article-card real-card reveal visible";
  const image = document.createElement("div");
  image.className = "article-image";
  const cover = document.createElement("img");
  cover.src = run.cover_url;
  cover.alt = `${run.title}封面`;
  const badge = document.createElement("span");
  badge.textContent = "真实生成";
  image.append(cover, badge);

  const body = document.createElement("div");
  body.className = "article-body";
  const meta = document.createElement("small");
  meta.textContent = `ARXIV · ${run.arxiv_id} · ${run.generated_at}`;
  const title = document.createElement("h3");
  title.textContent = run.title;
  const paperTitle = document.createElement("p");
  paperTitle.className = "paper-title";
  paperTitle.textContent = run.paper_title;
  const abstract = document.createElement("p");
  abstract.textContent = run.abstract || `检索主题：${run.query}`;
  const links = document.createElement("div");
  links.className = "artifact-links";
  [
    ["公众号预览", run.preview_url],
    ["论文 JSON", run.paper_json_url],
    ["总结 Markdown", run.markdown_url]
  ].forEach(([label, href]) => {
    const link = document.createElement("a");
    link.textContent = label;
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener";
    links.append(link);
  });
  body.append(meta, title, paperTitle, abstract, links);
  card.append(image, body);
  return card;
}

async function loadRealResults() {
  const container = document.querySelector("#real-results");
  try {
    const response = await fetch("/api/history", { cache: "no-store" });
    if (!response.ok) throw new Error("读取失败");
    const data = await response.json();
    const runs = data.runs || [];
    container.replaceChildren(...runs.map(buildResultCard));
    if (!runs.length) {
      const empty = document.createElement("p");
      empty.className = "results-loading";
      empty.textContent = "运行一次流水线后，真实结果会显示在这里。";
      container.append(empty);
    }
    document.querySelector("#real-run-count").textContent = String(runs.length);
    document.querySelector("#hero-run-count").textContent = String(runs.length);
    document.querySelector("#real-paper-count").textContent = String(new Set(runs.map((run) => run.arxiv_id)).size);
    document.querySelector("#real-cover-count").textContent = String(runs.filter((run) => run.cover_url).length);
    document.querySelector("#real-preview-count").textContent = String(runs.filter((run) => run.preview_url).length);
  } catch (_error) {
    container.innerHTML = '<p class="results-loading">启动真实流水线服务后可查看运行记录。</p>';
  }
}

function renderRunState(state) {
  state.steps.forEach((step, index) => {
    const item = runItems[index];
    item.className = step === "running" ? "running" : step === "done" ? "done" : "";
    item.querySelector("b").textContent = step === "running" ? "处理中" : step === "done" ? "完成" : "待命";
  });
  const completed = state.steps.filter((step) => step === "done").length;
  progressBar.style.width = `${(completed / runItems.length) * 100}%`;
  if (state.status === "running") {
    runnerStatus.textContent = `RUNNING ${Math.max(state.step + 1, 1)}/${runItems.length}`;
    return;
  }
  if (state.status === "complete") {
    runnerStatus.textContent = "COMPLETE";
    progressBar.style.width = "100%";
    updateResult(state.result);
    result.classList.add("show");
    runButton.disabled = false;
    runButton.textContent = "再次运行";
    loadRealResults();
    return;
  }
  if (state.status === "error") {
    runnerStatus.textContent = "ERROR";
    runButton.disabled = false;
    runButton.textContent = "重新运行";
    const active = runItems[Math.max(state.step, 0)];
    active.className = "running";
    active.querySelector("b").textContent = "失败";
    window.alert(state.error || "流水线运行失败");
  }
}

async function pollStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error("无法读取运行状态");
    const state = await response.json();
    renderRunState(state);
    if (state.status === "running") {
      statusTimer = setTimeout(pollStatus, 1200);
    }
  } catch (error) {
    runnerStatus.textContent = "OFFLINE";
    runButton.disabled = false;
    runButton.textContent = "开始运行";
    window.alert("真实运行需要通过 demo/server.py 启动网站。");
  }
}

runButton.addEventListener("click", async () => {
  resetRunner();
  runButton.disabled = true;
  runButton.textContent = "运行中…";
  runnerStatus.textContent = "STARTING";
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: topicInput.value.trim() })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "启动失败");
    pollStatus();
  } catch (error) {
    runnerStatus.textContent = "OFFLINE";
    runButton.disabled = false;
    runButton.textContent = "开始运行";
    window.alert(error.message === "Failed to fetch"
      ? "真实运行需要通过 demo/server.py 启动网站。"
      : error.message);
  }
});

if (window.location.protocol.startsWith("http")) {
  pollStatus();
  loadRealResults();
}
