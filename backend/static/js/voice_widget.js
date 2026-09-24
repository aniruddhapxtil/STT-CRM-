// =====================================================================
// DataPhi CRM — Null-Safe Voice Recording & Review Widget
// =====================================================================

(function () {
  let mediaRecorder = null;
  let audioChunks = [];
  let isRecording = false;

  // Initialize Voice Widget Elements
  function initVoiceWidget() {
    const micButtons = document.querySelectorAll(".mic-btn");
    micButtons.forEach((btn) => {
      btn.addEventListener("click", toggleRecording);
    });

    createModalDOM();
  }

  // Inject Review Modal if not present
  function createModalDOM() {
    if (document.getElementById("voiceReviewModal")) return;

    const modalHTML = `
      <div id="voiceReviewModal" style="display:none; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(15,23,42,0.6); backdrop-filter:blur(4px); align-items:center; justify-content:center;">
        <div style="background:#fff; border-radius:16px; width:540px; max-width:92%; max-height:85vh; overflow-y:auto; box-shadow:0 20px 40px rgba(0,0,0,0.2); border:1px solid #E2E8F0; padding:24px; font-family:inherit;">
          
          <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #E2E8F0; padding-bottom:12px; margin-bottom:16px;">
            <h3 style="margin:0; font-size:16px; font-weight:800; color:#1E2430;" id="modalTitle">Review Voice Extracted Fields</h3>
            <span id="modalIntentBadge" style="font-size:11px; font-weight:800; background:#F3F1FD; color:#4B3FC4; padding:3px 10px; border-radius:12px; text-transform:uppercase;">Account</span>
          </div>

          <div id="modalTranscriptBox" style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:12px; font-size:12.5px; color:#64748B; font-style:italic; margin-bottom:16px; line-height:1.5;"></div>

          <div id="modalFieldsContainer" style="display:flex; flex-direction:column; gap:12px; margin-bottom:20px;"></div>

          <div style="display:flex; justify-content:flex-end; gap:10px;">
            <button id="modalDismissBtn" style="padding:9px 16px; border:1.3px solid #E2E8F0; background:#fff; color:#64748B; border-radius:8px; font-weight:700; font-size:13px; cursor:pointer;">Dismiss</button>
            <button id="modalConfirmBtn" style="padding:9px 18px; border:none; background:#6C5CE7; color:#fff; border-radius:8px; font-weight:700; font-size:13px; cursor:pointer;">Confirm &amp; Fill Form</button>
          </div>
        </div>
      </div>
    `;
    document.body.insertAdjacentHTML("beforeend", modalHTML);

    document.getElementById("modalDismissBtn").addEventListener("click", () => {
      document.getElementById("voiceReviewModal").style.display = "none";
    });
  }

  // Handle Recording Toggle
  async function toggleRecording(e) {
    const btn = e.currentTarget;

    if (isRecording) {
      if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.stop();
      }
      isRecording = false;
      btn.style.transform = "scale(1)";
      btn.style.background = "#6C5CE7";
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunks = [];
      mediaRecorder = new MediaRecorder(stream);

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunks.push(event.data);
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        const audioBlob = new Blob(audioChunks, { type: "audio/webm" });
        await processAudio(audioBlob);
      };

      mediaRecorder.start();
      isRecording = true;
      btn.style.transform = "scale(1.1)";
      btn.style.background = "#EF4444";
    } catch (err) {
      alert("Microphone access error: " + err.message);
    }
  }

  // Send Audio to Backend API
  async function processAudio(blob) {
    const formData = new FormData();
    formData.append("audio", blob, "recording.webm");

    try {
      const res = await fetch("/api/voice/process", {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Audio processing failed");

      renderReviewModal(data);
    } catch (err) {
      alert("Voice Processing Error: " + err.message);
    }
  }

  // Render Modal with Null-Safe Fallbacks
  function renderReviewModal(data) {
    const modal = document.getElementById("voiceReviewModal");
    const container = document.getElementById("modalFieldsContainer");
    const transcriptBox = document.getElementById("modalTranscriptBox");
    const badge = document.getElementById("modalIntentBadge");

    transcriptBox.textContent = `"${data.transcript || "No speech detected"}"`;
    badge.textContent = data.intent || "EXTRACTED";
    container.innerHTML = "";

    // Null-safe access to nested entities
    const ext = data.extracted_data || {};
    const account = ext.account || {};
    const subsidiary = ext.subsidiary || {};
    const contact = ext.contact || {};
    const lead = ext.lead || {};
    const opportunity = ext.opportunity || {};

    const flatFields = [
      { label: "Account Name", val: account.account_name, targetId: "account_name" },
      { label: "Account Manager", val: account.account_manager, targetId: "account_manager" },
      { label: "Region", val: account.region, targetId: "region" },
      { label: "Industry", val: account.industry, targetId: "industry" },
      { label: "Subsidiary", val: subsidiary.subsidiary_name, targetId: "subsidiary_name" },
      { label: "Contact Name", val: contact.contact_name, targetId: "contact_name" },
      { label: "Designation", val: contact.designation, targetId: "designation" },
      { label: "Deal / Scope", val: opportunity.opportunity_name || lead.lead_name, targetId: "opportunity_name" },
      { label: "Deal Size (AED)", val: opportunity.deal_size || lead.deal_size, targetId: "deal_size" },
    ].filter((f) => f.val !== null && f.val !== undefined && String(f.val).trim() !== "");

    if (flatFields.length === 0) {
      container.innerHTML = `<div style="text-align:center; padding:16px; color:#64748B; font-size:13px;">No explicit entity details could be detected in this audio note.</div>`;
    } else {
      flatFields.forEach((item) => {
        const row = document.createElement("div");
        row.style.cssText = "display:flex; justify-content:space-between; align-items:center; background:#F8FAFC; border:1px solid #E2E8F0; padding:8px 12px; border-radius:6px; font-size:12.5px;";
        row.innerHTML = `
          <span style="font-weight:700; color:#64748B;">${item.label}</span>
          <span style="font-weight:700; color:#1E2430;">${item.val}</span>
        `;
        container.appendChild(row);
      });
    }

    // Populate Form upon confirmation
    const confirmBtn = document.getElementById("modalConfirmBtn");
    confirmBtn.onclick = () => {
      flatFields.forEach((item) => {
        const el = document.getElementById(item.targetId);
        if (el) el.value = item.val;
      });
      modal.style.display = "none";
      if (typeof showCreateForm === "function") showCreateForm();
    };

    modal.style.display = "flex";
  }

  document.addEventListener("DOMContentLoaded", initVoiceWidget);
})();