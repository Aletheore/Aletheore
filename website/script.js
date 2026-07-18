const STAT_LABELS = {
  moduleCount: "modules",
  dependencyEdgeCount: "dependency edges",
  clusterCount: "clusters",
  totalCommits: "commits scanned",
  licenseFindingsCount: "license findings",
  vulnerabilityFindingsCount: "vulnerability findings",
  scanSeconds: "seconds to scan",
  evidenceJsonBytes: "JSON bytes",
  evidenceToonBytes: "TOON bytes",
};

function formatStatValue(value) {
  return typeof value === "number" ? value.toLocaleString() : value;
}

function renderShowcaseCards() {
  document.querySelectorAll(".showcase-card").forEach((card) => {
    const repo = card.dataset.repo;
    const data = SHOWCASE[repo];
    const list = card.querySelector(".showcase-stats");
    const fields = list.dataset.fields.split(",");
    list.innerHTML = fields
      .map((field) => `<li><strong>${formatStatValue(data[field])}</strong> ${STAT_LABELS[field]}</li>`)
      .join("");
  });
}

function animateProofZoneOnScroll() {
  const proofZone = document.getElementById("proof-zone");
  if (!proofZone || !window.Motion) return;

  Motion.inView(
    proofZone,
    () => {
      Motion.animate(
        proofZone,
        { opacity: [0, 1], transform: ["translateY(24px)", "translateY(0)"] },
        { duration: 0.6, easing: "ease-out" }
      );
      animateTokenCounters();
    },
    { amount: 0.3 }
  );
}

function animateTokenCounters() {
  document.querySelectorAll(".toon-counter-value").forEach((el) => {
    const target = parseFloat(el.dataset.target);
    Motion.animate(0, target, {
      duration: 1.2,
      easing: "ease-out",
      onUpdate: (latest) => {
        el.textContent = `${latest.toFixed(1)}%`;
      },
    });
  });
}

renderShowcaseCards();
animateProofZoneOnScroll();
