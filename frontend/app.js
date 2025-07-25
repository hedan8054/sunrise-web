// 简单用 GitHub Contents API 列出 data/requests 下的 JSON
// 需要 repo 是 public，或者用户本地填入 token（此处不做）

async function listFiles(owner, repo) {
  const url = `https://api.github.com/repos/${owner}/${repo}/contents/data/requests`;
  const r = await fetch(url);
  if (!r.ok) throw new Error("无法列出文件，请确认 repo/路径 是否存在 & public");
  const arr = await r.json();
  return arr.filter(f => f.name.endsWith(".json"));
}

async function fetchFile(rawUrl) {
  const r = await fetch(rawUrl);
  if (!r.ok) throw new Error("文件下载失败");
  return await r.json();
}

document.getElementById("loadList").addEventListener("click", async () => {
  const owner = document.getElementById("owner").value.trim();
  const repo  = document.getElementById("repo").value.trim();
  const ul = document.getElementById("fileList");
  ul.innerHTML = "加载中...";
  try {
    const files = await listFiles(owner, repo);
    ul.innerHTML = "";
    files.sort((a,b)=> a.name.localeCompare(b.name)).reverse();
    files.forEach(f => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = "#";
      a.textContent = f.name;
      a.addEventListener("click", async (e) => {
        e.preventDefault();
        const data = await fetchFile(f.download_url);
        showResult(data);
      });
      li.appendChild(a);
      ul.appendChild(li);
    });
  } catch (err) {
    ul.innerHTML = "加载失败：" + err.message;
  }
});

function showResult(data) {
  document.getElementById("viewer").style.display = "block";
  document.getElementById("jsonRaw").textContent = JSON.stringify(data, null, 2);
  document.getElementById("sceneText").textContent = data.text.scene || "";
  document.getElementById("detailText").textContent = data.text.detail || "";
}
