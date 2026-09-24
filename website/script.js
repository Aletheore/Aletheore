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
      .map(
        (field) =>
          `<li><span>${STAT_LABELS[field]}</span><strong>${formatStatValue(data[field])}</strong></li>`
      )
      .join("");
  });
}

function animateProofZoneOnScroll() {
  const proofZone = document.getElementById("proof-zone");
  if (!proofZone || !window.Motion) return;

  const stopWatching = Motion.inView(
    proofZone,
    () => {
      Motion.animate(
        proofZone,
        { opacity: [0, 1], transform: ["translateY(24px)", "translateY(0)"] },
        { duration: 0.6, easing: "ease-out" }
      );
      animateTokenCounters();
      stopWatching();
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

function revealOnScroll() {
  const targets = document.querySelectorAll("[data-reveal]");
  if (!targets.length) return;

  if (!window.IntersectionObserver) {
    targets.forEach((el) => el.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries, obs) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          obs.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.15, rootMargin: "0px 0px -40px 0px" }
  );

  targets.forEach((el) => observer.observe(el));
}

function shadeNavOnScroll() {
  const nav = document.querySelector(".site-nav");
  if (!nav) return;

  const update = () => {
    nav.classList.toggle("is-scrolled", window.scrollY > 40);
  };
  update();
  window.addEventListener("scroll", update, { passive: true });
}

function setupFollowDialog() {
  const dialog = document.getElementById("follow-dialog");
  // Without <dialog> support the trigger stays a plain link to LinkedIn.
  if (!dialog || typeof dialog.showModal !== "function") return;

  document.querySelectorAll("[data-follow-open]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      dialog.showModal();
    });
  });

  // A click on the backdrop lands on the dialog element itself (the padding
  // lives on the inner body), so that is the "click outside" signal.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-follow-close]")) {
      dialog.close();
    }
  });
}

renderShowcaseCards();
animateProofZoneOnScroll();
revealOnScroll();
shadeNavOnScroll();
setupFollowDialog();
