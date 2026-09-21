// ==========================================================================
// Nemotron C# Studio - Web UI Client Logic
// ==========================================================================

document.addEventListener("DOMContentLoaded", () => {
  // State
  let currentScanData = null;
  let logPollingInterval = null;
  let currentSonarIssues = [];

  // DOM Elements - Navigation & Views
  const scannerView = document.getElementById("scannerView");
  const workspaceView = document.getElementById("workspaceView");
  const navWorkspaceInfo = document.getElementById("navWorkspaceInfo");
  const activeProjectName = document.getElementById("activeProjectName");
  const btnRescan = document.getElementById("btnRescan");

  // DOM Elements - Scanner
  const scanForm = document.getElementById("scanForm");
  const projectPathInput = document.getElementById("projectPathInput");
  const btnStartScan = document.getElementById("btnStartScan");
  const existingScanCard = document.getElementById("existingScanCard");
  const btnLoadExistingScan = document.getElementById("btnLoadExistingScan");
  const scanResultsContainer = document.getElementById("scanResultsContainer");
  const statProjects = document.getElementById("statProjects");
  const statFiles = document.getElementById("statFiles");
  const statTypes = document.getElementById("statTypes");

  // DOM Elements - Action Choice Modal
  const actionChoiceModal = document.getElementById("actionChoiceModal");
  const modalProjectCount = document.getElementById("modalProjectCount");
  const modalFileCount = document.getElementById("modalFileCount");
  const btnChooseTestGen = document.getElementById("btnChooseTestGen");
  const btnChooseSonar = document.getElementById("btnChooseSonar");

  // DOM Elements - Tabs
  const tabButtons = document.querySelectorAll(".tab-btn");
  const tabContents = document.querySelectorAll(".tab-content");

  // DOM Elements - TestGen Workspace
  const scaffoldProjectSelect = document.getElementById("scaffoldProjectSelect");
  const btnScaffold = document.getElementById("btnScaffold");
  const scaffoldBadge = document.getElementById("scaffoldBadge");
  const scaffoldResultMsg = document.getElementById("scaffoldResultMsg");
  const segmentBtns = document.querySelectorAll(".segment-btn");
  const singleModeForm = document.getElementById("singleModeForm");
  const batchModeForm = document.getElementById("batchModeForm");
  const singleFileSelect = document.getElementById("singleFileSelect");
  const providerSelect = document.getElementById("providerSelect");
  const modelSelect = document.getElementById("modelSelect");
  const coverageSlider = document.getElementById("coverageSlider");
  const coverageVal = document.getElementById("coverageVal");
  const retriesInput = document.getElementById("retriesInput");
  const btnRunSingleTestGen = document.getElementById("btnRunSingleTestGen");
  const batchProjectSelect = document.getElementById("batchProjectSelect");
  const batchProviderSelect = document.getElementById("batchProviderSelect");
  const batchModelSelect = document.getElementById("batchModelSelect");
  const concurrencySelect = document.getElementById("concurrencySelect");
  const batchResumeCheck = document.getElementById("batchResumeCheck");
  const batchForceCheck = document.getElementById("batchForceCheck");
  const btnRunBatchTestGen = document.getElementById("btnRunBatchTestGen");

  // DOM Elements - Sonar Workspace
  const sonarHostInput = document.getElementById("sonarHostInput");
  const sonarTokenInput = document.getElementById("sonarTokenInput");
  const btnToggleToken = document.getElementById("btnToggleToken");
  const sonarProjectKeyInput = document.getElementById("sonarProjectKeyInput");
  const btnFetchSonarProjects = document.getElementById("btnFetchSonarProjects");
  const sonarBranchInput = document.getElementById("sonarBranchInput");
  const btnTestSonarConn = document.getElementById("btnTestSonarConn");
  const btnLoadMockSonar = document.getElementById("btnLoadMockSonar");
  const btnFetchSonarIssues = document.getElementById("btnFetchSonarIssues");
  const sonarConnBadge = document.getElementById("sonarConnBadge");
  const sonarIssuesCard = document.getElementById("sonarIssuesCard");
  const issueCount = document.getElementById("issueCount");
  const sonarIssuesTbody = document.getElementById("sonarIssuesTbody");

  // DOM Elements - Terminal
  const terminalDrawer = document.getElementById("terminalDrawer");
  const terminalOutput = document.getElementById("terminalOutput");
  const btnToggleTerminal = document.getElementById("btnToggleTerminal");
  const btnCloseTerminal = document.getElementById("btnCloseTerminal");
  const btnClearTerminal = document.getElementById("btnClearTerminal");

  // ==========================================================================
  // Terminal Logger Helper
  // ==========================================================================
  function logToTerminal(message, type = "info") {
    const line = document.createElement("div");
    line.className = `term-line ${type}`;
    const timestamp = new Date().toLocaleTimeString();
    line.textContent = `[${timestamp}] ${message}`;
    terminalOutput.appendChild(line);
    terminalOutput.scrollTop = terminalOutput.scrollHeight;
  }

  function openTerminal() {
    terminalDrawer.classList.add("open");
  }

  btnToggleTerminal.addEventListener("click", () => {
    terminalDrawer.classList.toggle("open");
  });

  btnCloseTerminal.addEventListener("click", () => {
    terminalDrawer.classList.remove("open");
  });

  btnClearTerminal.addEventListener("click", () => {
    terminalOutput.innerHTML = "";
    logToTerminal("Console cleared.", "sys");
  });

  // Start polling server execution logs
  function startLogPolling() {
    if (logPollingInterval) clearInterval(logPollingInterval);
    logPollingInterval = setInterval(async () => {
      try {
        const res = await fetch("/api/logs");
        if (res.ok) {
          const data = await res.json();
          if (data.lines && data.lines.length > 0) {
            data.lines.forEach((l) => logToTerminal(l.msg, l.type || "info"));
          }
        }
      } catch (err) {
        // quiet polling error
      }
    }, 1500);
  }

  async function waitForJob(jobId) {
    while (true) {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Unable to read job status.");
      if (data.status !== "RUNNING") return data;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  startLogPolling();

  // ==========================================================================
  // Provider & Model Dynamic Loading
  // ==========================================================================
  let providerData = null;

  function populateModelsForSelect(selectEl, providerKey) {
    if (!selectEl) return;
    selectEl.innerHTML = "";
    if (!providerData || !providerData.providers || !providerData.providers[providerKey]) {
      const opt = document.createElement("option");
      opt.value = providerKey === "gemini" ? "gemini-3.6-flash" : "qwen3-coder:latest";
      opt.textContent = opt.value;
      selectEl.appendChild(opt);
      return;
    }
    const info = providerData.providers[providerKey];
    info.models.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      selectEl.appendChild(opt);
    });
    if (info.default_model) {
      selectEl.value = info.default_model;
    }
  }

  if (providerSelect) {
    providerSelect.addEventListener("change", () => {
      populateModelsForSelect(modelSelect, providerSelect.value);
    });
  }

  if (batchProviderSelect) {
    batchProviderSelect.addEventListener("change", () => {
      populateModelsForSelect(batchModelSelect, batchProviderSelect.value);
    });
  }

  // ==========================================================================
  // Initial Page Load: Check Defaults & Scan Manifest
  // ==========================================================================
  async function init() {
    try {
      // 0. Fetch AI Providers and Models
      try {
        const provRes = await fetch("/api/providers/models");
        if (provRes.ok) {
          providerData = await provRes.json();
          if (providerData.default_provider) {
            if (providerSelect) providerSelect.value = providerData.default_provider;
            if (batchProviderSelect) batchProviderSelect.value = providerData.default_provider;
          }
          if (providerSelect) populateModelsForSelect(modelSelect, providerSelect.value);
          if (batchProviderSelect) populateModelsForSelect(batchModelSelect, batchProviderSelect.value);
        }
      } catch (err) {
        console.warn("Could not load AI providers", err);
      }

      // 1. Fetch server config defaults (.env)
      const cfgRes = await fetch("/api/config");
      if (cfgRes.ok) {
        const cfg = await cfgRes.json();
        if (cfg.project_root) projectPathInput.value = cfg.project_root;
        if (cfg.sonar_host) sonarHostInput.value = cfg.sonar_host;
        if (cfg.sonar_token) sonarTokenInput.value = cfg.sonar_token;
        if (cfg.sonar_project_key) sonarProjectKeyInput.value = cfg.sonar_project_key;
      }

      // 2. Check if a scan already exists
      const scanRes = await fetch("/api/scan-data");
      if (scanRes.ok) {
        const data = await scanRes.json();
        if (data && data.projects && data.projects.length > 0) {
          existingScanCard.style.display = "flex";
          currentScanData = data;
        }
      }
    } catch (err) {
      logToTerminal(`Initialization error: ${err.message}`, "error");
    }
  }

  init();

  // ==========================================================================
  // Scanner Execution
  // ==========================================================================
  scanForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const targetPath = projectPathInput.value.trim() || ".";
    
    // UI state: loading
    btnStartScan.disabled = true;
    btnStartScan.querySelector(".spinner").style.display = "inline-block";
    btnStartScan.querySelector(".btn-text").textContent = "Scanning Solution...";
    openTerminal();
    logToTerminal(`Starting project scan for: ${targetPath}`, "sys");

    try {
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: targetPath }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to scan project.");

      currentScanData = data;
      logToTerminal(`Scan successful! Found ${data.projects.length} project(s).`, "success");

      renderScanResults(data);
      showActionChoiceModal(data);

    } catch (err) {
      logToTerminal(`Scan Error: ${err.message}`, "error");
      alert(`Scan failed: ${err.message}`);
    } finally {
      btnStartScan.disabled = false;
      btnStartScan.querySelector(".spinner").style.display = "none";
      btnStartScan.querySelector(".btn-text").textContent = "Scan Project";
    }
  });

  btnLoadExistingScan.addEventListener("click", () => {
    if (currentScanData) {
      renderScanResults(currentScanData);
      showActionChoiceModal(currentScanData);
    }
  });

  btnRescan.addEventListener("click", () => {
    scannerView.classList.add("active");
    workspaceView.style.display = "none";
    navWorkspaceInfo.style.display = "none";
  });

  function renderScanResults(manifest) {
    const totalProjects = manifest.projects ? manifest.projects.length : 0;
    const totalFiles = manifest.projects ? manifest.projects.reduce((acc, p) => acc + (p.files ? p.files.length : 0), 0) : 0;
    const totalTypes = manifest.projects ? manifest.projects.reduce((acc, p) => acc + p.files.reduce((fa, f) => fa + (f.types ? f.types.length : 0), 0), 0) : 0;

    statProjects.textContent = totalProjects;
    statFiles.textContent = totalFiles;
    statTypes.textContent = totalTypes;
    scanResultsContainer.style.display = "grid";

    // Populate selectors in TestGen workspace
    populateTestGenSelectors(manifest);

    // Set navbar workspace pill
    const firstProj = manifest.projects && manifest.projects[0] ? manifest.projects[0].project_name : "Solution";
    activeProjectName.textContent = firstProj;
    navWorkspaceInfo.style.display = "flex";
  }

  function showActionChoiceModal(manifest) {
    const totalProjects = manifest.projects ? manifest.projects.length : 0;
    const totalFiles = manifest.projects ? manifest.projects.reduce((acc, p) => acc + (p.files ? p.files.length : 0), 0) : 0;

    modalProjectCount.textContent = totalProjects;
    modalFileCount.textContent = totalFiles;
    actionChoiceModal.style.display = "flex";
  }

  btnChooseTestGen.addEventListener("click", () => {
    actionChoiceModal.style.display = "none";
    scannerView.classList.remove("active");
    workspaceView.style.display = "block";
    switchTab("testgenTab");
  });

  btnChooseSonar.addEventListener("click", () => {
    actionChoiceModal.style.display = "none";
    scannerView.classList.remove("active");
    workspaceView.style.display = "block";
    switchTab("sonarTab");
  });

  // ==========================================================================
  // Workspace Tab Switching
  // ==========================================================================
  function switchTab(targetTabId) {
    tabButtons.forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === targetTabId);
    });
    tabContents.forEach((content) => {
      content.classList.toggle("active", content.id === targetTabId);
    });
  }

  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      switchTab(btn.dataset.tab);
    });
  });

  // ==========================================================================
  // TestGen: Project Scaffolding & Form Population
  // ==========================================================================
  function populateTestGenSelectors(manifest) {
    // 1. Scaffold project select
    scaffoldProjectSelect.innerHTML = '<option value="">Select target project...</option>';
    batchProjectSelect.innerHTML = '<option value="ALL">All Projects (Sequential)</option>';
    singleFileSelect.innerHTML = '<option value="">Choose a scanned file...</option>';

    if (!manifest || !manifest.projects) return;

    manifest.projects.forEach((proj) => {
      const opt = document.createElement("option");
      opt.value = proj.project_name;
      opt.textContent = `${proj.project_name} (${proj.files.length} files)`;
      scaffoldProjectSelect.appendChild(opt);

      const batchOpt = opt.cloneNode(true);
      batchProjectSelect.appendChild(batchOpt);

      // Files for single mode
      proj.files.forEach((file) => {
        const fOpt = document.createElement("option");
        fOpt.value = file.file_name;
        fOpt.textContent = `[${proj.project_name}] ${file.relative_path || file.file_name}`;
        singleFileSelect.appendChild(fOpt);
      });
    });

    if (manifest.projects.length > 0) {
      scaffoldProjectSelect.selectedIndex = 1;
    }
  }

  btnScaffold.addEventListener("click", async () => {
    const selectedProj = scaffoldProjectSelect.value;
    if (!selectedProj) {
      alert("Please select a target project to scaffold.");
      return;
    }

    btnScaffold.disabled = true;
    btnScaffold.querySelector(".spinner").style.display = "inline-block";
    openTerminal();
    logToTerminal(`Scaffolding test project for: ${selectedProj}`, "sys");

    try {
      const res = await fetch("/api/scaffold", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_name: selectedProj }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to scaffold test project.");

      scaffoldBadge.textContent = "Ready";
      scaffoldBadge.className = "status-badge status-ready";
      scaffoldResultMsg.className = "inline-msg success";
      scaffoldResultMsg.innerHTML = `<strong>Test Project Ready:</strong> <code>${data.test_csproj}</code>`;
      scaffoldResultMsg.style.display = "block";
      logToTerminal(`Test project scaffolded successfully: ${data.test_csproj}`, "success");

    } catch (err) {
      scaffoldResultMsg.className = "inline-msg error";
      scaffoldResultMsg.textContent = `Scaffolding Error: ${err.message}`;
      scaffoldResultMsg.style.display = "block";
      logToTerminal(`Scaffold failed: ${err.message}`, "error");
    } finally {
      btnScaffold.disabled = false;
      btnScaffold.querySelector(".spinner").style.display = "none";
    }
  });

  // Single / Batch Mode Toggle
  segmentBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      segmentBtns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const mode = btn.dataset.mode;
      if (mode === "single") {
        singleModeForm.classList.add("active");
        batchModeForm.classList.remove("active");
      } else {
        singleModeForm.classList.remove("active");
        batchModeForm.classList.add("active");
      }
    });
  });

  coverageSlider.addEventListener("input", () => {
    coverageVal.textContent = `${coverageSlider.value}%`;
  });

  // Run Single Test Gen
  btnRunSingleTestGen.addEventListener("click", async () => {
    const targetFile = singleFileSelect.value;
    if (!targetFile) {
      alert("Please select a C# file to generate tests for.");
      return;
    }

    const payload = {
      file_name: targetFile,
      provider: providerSelect ? providerSelect.value : "ollama",
      model: modelSelect ? modelSelect.value : "qwen3-coder:latest",
      coverage: parseFloat(coverageSlider.value),
      retries: parseInt(retriesInput.value, 10),
    };

    btnRunSingleTestGen.disabled = true;
    openTerminal();
    logToTerminal(`Starting Single-File Test Generation for: ${targetFile}`, "sys");

    try {
      const res = await fetch("/api/testgen/single", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      let data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed test generation.");
      if (data.status === "RUNNING") data = await waitForJob(data.job_id);
      if (data.status === "ERROR") throw new Error(data.message || "Test generation failed.");

      logToTerminal(`Result for ${targetFile}: ${data.status} (Coverage: ${data.coverage_pct}%, Iterations: ${data.iterations})`, data.status === "SUCCESS" ? "success" : "warn");
      if (data.test_file) {
        logToTerminal(`Generated Test File: ${data.test_file}`, "info");
      }
    } catch (err) {
      logToTerminal(`TestGen Error: ${err.message}`, "error");
    } finally {
      btnRunSingleTestGen.disabled = false;
    }
  });

  // Run Batch Test Gen
  btnRunBatchTestGen.addEventListener("click", async () => {
    const payload = {
      project_name: batchProjectSelect.value,
      concurrency: parseInt(concurrencySelect.value, 10),
      resume: batchResumeCheck.checked,
      force: batchForceCheck.checked,
      coverage: parseFloat(coverageSlider.value),
      retries: parseInt(retriesInput.value, 10),
      provider: batchProviderSelect ? batchProviderSelect.value : "ollama",
      model: batchModelSelect ? batchModelSelect.value : "qwen3-coder:latest",
    };

    btnRunBatchTestGen.disabled = true;
    openTerminal();
    logToTerminal(`Starting Batch Test Generation (${payload.project_name})...`, "sys");

    try {
      const res = await fetch("/api/testgen/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed batch test generation.");

      logToTerminal(`Batch process launched in background (PID: ${data.pid || "Active"}). Logs streaming below:`, "success");
    } catch (err) {
      logToTerminal(`Batch TestGen Error: ${err.message}`, "error");
    } finally {
      btnRunBatchTestGen.disabled = false;
    }
  });

  // ==========================================================================
  // SonarQube Workspace Actions
  // ==========================================================================
  btnToggleToken.addEventListener("click", () => {
    if (sonarTokenInput.type === "password") {
      sonarTokenInput.type = "text";
      btnToggleToken.textContent = "Hide";
    } else {
      sonarTokenInput.type = "password";
      btnToggleToken.textContent = "Show";
    }
  });

  btnTestSonarConn.addEventListener("click", async () => {
    const host = sonarHostInput.value.trim();
    const token = sonarTokenInput.value.trim();

    openTerminal();
    logToTerminal(`Testing SonarQube connection at: ${host}`, "sys");

    try {
      const res = await fetch("/api/sonar/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host_url: host, token: token }),
      });

      const data = await res.json();
      if (data.connected) {
        sonarConnBadge.textContent = "Connected";
        sonarConnBadge.className = "status-badge status-ready";
        logToTerminal(`SonarQube Connected! Server Version: ${data.server_version}, Auth: ${data.authenticated}`, "success");
      } else {
        sonarConnBadge.textContent = "Disconnected";
        sonarConnBadge.className = "status-badge status-idle";
        logToTerminal(`Could not connect to SonarQube: ${JSON.stringify(data.details)}`, "warn");
      }
    } catch (err) {
      logToTerminal(`Sonar connection error: ${err.message}`, "error");
    }
  });

  if (btnLoadMockSonar) {
    btnLoadMockSonar.addEventListener("click", () => {
      sonarHostInput.value = "mock";
      sonarTokenInput.value = "mock-token-demo";
      sonarProjectKeyInput.value = "MockCommerce";
      btnTestSonarConn.click();
      setTimeout(() => {
        btnFetchSonarIssues.click();
      }, 600);
    });
  }

  btnFetchSonarProjects.addEventListener("click", async () => {
    const host = sonarHostInput.value.trim();
    const token = sonarTokenInput.value.trim();

    openTerminal();
    logToTerminal(`Fetching projects from SonarQube...`, "sys");

    try {
      const res = await fetch("/api/sonar/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host_url: host, token: token }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to fetch projects.");

      if (data.projects && data.projects.length > 0) {
        sonarProjectKeyInput.value = data.projects[0].key;
        logToTerminal(`Found ${data.projects.length} project(s). Selected: ${data.projects[0].key}`, "success");
      } else {
        logToTerminal("No projects found on SonarQube server.", "warn");
      }
    } catch (err) {
      logToTerminal(`Failed to list projects: ${err.message}`, "error");
    }
  });

  btnFetchSonarIssues.addEventListener("click", async () => {
    const host = sonarHostInput.value.trim();
    const token = sonarTokenInput.value.trim();
    const projectKey = sonarProjectKeyInput.value.trim();
    const branch = sonarBranchInput.value.trim();

    if (!projectKey) {
      alert("Please enter or fetch a SonarQube project key.");
      return;
    }

    btnFetchSonarIssues.disabled = true;
    openTerminal();
    logToTerminal(`Fetching Cognitive Complexity (S3776) issues for '${projectKey}'...`, "sys");

    try {
      const res = await fetch("/api/sonar/issues", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host_url: host, token: token, project_key: projectKey, branch: branch }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to fetch issues.");

      currentSonarIssues = data.issues || [];
      issueCount.textContent = currentSonarIssues.length;
      sonarIssuesCard.style.display = "block";
      renderSonarIssuesTable(currentSonarIssues);

      logToTerminal(`Found ${currentSonarIssues.length} Cognitive Complexity issue(s).`, currentSonarIssues.length > 0 ? "warn" : "success");

    } catch (err) {
      logToTerminal(`Error fetching Sonar issues: ${err.message}`, "error");
    } finally {
      btnFetchSonarIssues.disabled = false;
    }
  });

  function renderSonarIssuesTable(issues) {
    sonarIssuesTbody.innerHTML = "";

    if (issues.length === 0) {
      sonarIssuesTbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #94a3b8; padding: 2rem;">No Cognitive Complexity issues found! Quality Gate is passing.</td></tr>';
      return;
    }

    issues.forEach((issue) => {
      const tr = document.createElement("tr");
      const stats = issue.complexity_stats || { current: 0, allowed: 15 };
      const isHigh = stats.current > 20;

      tr.innerHTML = `
        <td>
          <div style="font-weight: 600; color: #fff;">${issue.file_path}</div>
          <div style="font-size: 0.8rem; color: #94a3b8;">Line ${issue.line}</div>
        </td>
        <td>
          <span class="complexity-pill ${isHigh ? 'complexity-high' : 'complexity-med'}">
            ${stats.current} / max ${stats.allowed}
          </span>
        </td>
        <td style="color: #cbd5e1; font-size: 0.85rem;">
          ${issue.flows && issue.flows.length > 0 ? `${issue.flows.length} hotspot(s)` : "General nesting"}
        </td>
        <td>
          <span class="status-badge status-idle" id="badge-${issue.key}">Pending</span>
        </td>
        <td>
          <div style="display: flex; gap: 0.5rem;">
            <button class="btn btn-secondary btn-sm" onclick="window.runSonarAction('${issue.key}', true)">Dry-Run</button>
            <button class="btn btn-primary btn-sm" onclick="window.runSonarAction('${issue.key}', false)">Auto-Fix & Verify</button>
          </div>
        </td>
      `;
      sonarIssuesTbody.appendChild(tr);
    });
  }

  window.runSonarAction = async (issueKey, dryRun) => {
    const badge = document.getElementById(`badge-${issueKey}`);
    if (badge) {
      badge.textContent = dryRun ? "Previewing..." : "Fixing...";
      badge.className = "status-badge";
    }

    openTerminal();
    logToTerminal(`[${dryRun ? 'DRY-RUN' : 'AUTO-FIX'}] Starting issue resolution: ${issueKey}...`, "sys");

    try {
      const res = await fetch("/api/sonar/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          issue_key: issueKey,
          dry_run: dryRun,
          host_url: sonarHostInput.value.trim(),
          token: sonarTokenInput.value.trim(),
          project_key: sonarProjectKeyInput.value.trim(),
        }),
      });

      let data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed resolving issue.");
      if (data.status === "RUNNING") data = await waitForJob(data.job_id);
      if (data.status === "ERROR") throw new Error(data.message || "Failed resolving issue.");

      if (badge) {
        if (data.status === "VERIFIED") {
          badge.textContent = "Verified ✅";
          badge.className = "status-badge status-ready";
          logToTerminal(`Issue ${issueKey} VERIFIED! Refactoring passed build & test regression check.`, "success");
        } else if (data.status === "DRY_RUN") {
          badge.textContent = "Dry-Run OK";
          badge.className = "status-badge status-ready";
          logToTerminal(`[DRY-RUN] Matched method: ${data.method}() in ${data.file} (Lines ${data.lines})`, "info");
        } else {
          badge.textContent = "Failed ❌";
          badge.className = "status-badge complexity-high";
          logToTerminal(`Issue ${issueKey} FAILED: ${data.message || JSON.stringify(data.errors)}`, "error");
        }
      }
    } catch (err) {
      if (badge) {
        badge.textContent = "Error";
        badge.className = "status-badge complexity-high";
      }
      logToTerminal(`Error on issue ${issueKey}: ${err.message}`, "error");
    }
  };

});
