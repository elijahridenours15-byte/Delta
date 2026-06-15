async function loadManifest() {
  const response = await fetch("assets/project-manifest.json");
  if (!response.ok) {
    throw new Error("Unable to load project manifest");
  }
  return response.json();
}

function updateStats(data) {
  const statPages = document.getElementById("stat-pages");
  const statRoutes = document.getElementById("stat-routes");
  const statFiles = document.getElementById("stat-files");

  if (statPages) statPages.textContent = String(data.pages.length);
  if (statRoutes) statRoutes.textContent = String(data.routes.length);
  if (statFiles) statFiles.textContent = String(data.files.length);
}

function renderStack(data) {
  const stackEl = document.getElementById("stack-chips");
  if (!stackEl) return;

  stackEl.innerHTML = data.stack
    .map((item) => `<span class="chip">${item}</span>`)
    .join("");
}

function pageTag(mode) {
  return mode === "backend-assisted" ? "tag-signal" : "tag-accent";
}

function renderHome(data) {
  const pageGrid = document.getElementById("page-grid");
  if (pageGrid) {
    pageGrid.innerHTML = data.pages
      .map(
        (page) => `
          <article class="module-card">
            <h3>${page.name}</h3>
            <p>${page.summary}</p>
            <div class="module-meta">
              <span class="tag ${pageTag(page.mode)}">${page.mode}</span>
              <span class="tag">${page.template}</span>
              <span class="tag">${page.route}</span>
            </div>
          </article>
        `
      )
      .join("");
  }

  const routePreview = document.getElementById("route-preview");
  if (routePreview) {
    routePreview.innerHTML = data.routes
      .slice(0, 8)
      .map(
        (route) => `
          <article class="route-card">
            <h3>${route.path}</h3>
            <p>${route.summary}</p>
            <div class="route-meta">
              <span class="tag">${route.method}</span>
              <span class="tag">${route.kind}</span>
            </div>
          </article>
        `
      )
      .join("");
  }
}

function renderWorkbench(data) {
  const groups = ["all", ...new Set(data.files.map((file) => file.group))];
  const filtersEl = document.getElementById("file-filters");
  const fileGrid = document.getElementById("file-grid");

  function drawFiles(activeGroup) {
    const visibleFiles =
      activeGroup === "all"
        ? data.files
        : data.files.filter((file) => file.group === activeGroup);

    fileGrid.innerHTML = visibleFiles
      .map(
        (file) => `
          <article class="file-card">
            <h3>${file.path}</h3>
            <p>${file.summary}</p>
            <div class="file-meta">
              <span class="tag tag-accent">${file.group}</span>
            </div>
          </article>
        `
      )
      .join("");
  }

  if (filtersEl) {
    filtersEl.innerHTML = groups
      .map(
        (group, index) =>
          `<button class="${index === 0 ? "active" : ""}" data-group="${group}">${group}</button>`
      )
      .join("");

    filtersEl.addEventListener("click", (event) => {
      const target = event.target.closest("button[data-group]");
      if (!target) return;

      filtersEl.querySelectorAll("button").forEach((button) => button.classList.remove("active"));
      target.classList.add("active");
      drawFiles(target.dataset.group);
    });
  }

  if (fileGrid) {
    drawFiles("all");
  }

  const treeView = document.getElementById("tree-view");
  if (treeView) {
    treeView.textContent = data.tree.join("\n");
  }

  const pageList = document.getElementById("page-list");
  if (pageList) {
    pageList.innerHTML = data.pages
      .map(
        (page) => `
          <article>
            <h3>${page.name}</h3>
            <p>${page.summary}</p>
            <div class="module-meta">
              <span class="tag">${page.route}</span>
              <span class="tag ${pageTag(page.mode)}">${page.mode}</span>
            </div>
          </article>
        `
      )
      .join("");
  }

  const routeTableBody = document.getElementById("route-table-body");
  if (routeTableBody) {
    routeTableBody.innerHTML = data.routes
      .map(
        (route) => `
          <tr>
            <td>${route.method}</td>
            <td>${route.path}</td>
            <td>${route.kind}</td>
            <td>${route.summary}</td>
          </tr>
        `
      )
      .join("");
  }
}

function renderDeploy(data) {
  const productGrid = document.getElementById("product-grid");
  if (productGrid) {
    productGrid.innerHTML = data.deployment.currentProducts
      .map(
        (product) => `
          <article class="module-card">
            <h3>${product.name}</h3>
            <p>${product.role}</p>
          </article>
        `
      )
      .join("");
  }

  const freePath = document.getElementById("free-path");
  if (freePath) {
    freePath.innerHTML = data.deployment.freePath.map((step) => `<li>${step}</li>`).join("");
  }

  const upgradePath = document.getElementById("upgrade-path");
  if (upgradePath) {
    upgradePath.innerHTML = data.deployment.upgradePath.map((step) => `<li>${step}</li>`).join("");
  }

  const deploymentNotes = document.getElementById("deployment-notes");
  if (deploymentNotes) {
    deploymentNotes.innerHTML = data.deployment.notes.map((note) => `<li>${note}</li>`).join("");
  }
}

async function init() {
  try {
    const data = await loadManifest();
    updateStats(data);
    renderStack(data);

    const page = document.body.dataset.page;
    if (page === "home") renderHome(data);
    if (page === "workbench") renderWorkbench(data);
    if (page === "deploy") renderDeploy(data);
  } catch (error) {
    const shell = document.querySelector(".page-shell");
    if (shell) {
      shell.insertAdjacentHTML(
        "afterbegin",
        `<section class="panel" style="padding:1.25rem"><h2>Site data unavailable</h2><p>${error.message}</p></section>`
      );
    }
  }
}

document.addEventListener("DOMContentLoaded", init);