const CHART_COLORS = {
  accent: "#e0863a",
  muted: "#7d7568",
  grid: "rgba(243, 238, 227, 0.08)",
  text: "#b9b1a4",
  textStrong: "#efe7d8",
};

const CHART_FONT = {
  family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, sans-serif",
  size: 12.5,
};

function baseBarOptions(maxValue) {
  return {
    indexAxis: "y",
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 500 },
    layout: { padding: { right: 12 } },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#0e0c09",
        borderColor: "rgba(243, 238, 227, 0.12)",
        borderWidth: 1,
        titleColor: CHART_COLORS.textStrong,
        bodyColor: CHART_COLORS.text,
        padding: 10,
        callbacks: {
          label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.x.toFixed(1)}%`,
        },
      },
    },
    scales: {
      x: {
        min: 0,
        max: maxValue,
        grid: { color: CHART_COLORS.grid },
        border: { display: false },
        ticks: {
          color: CHART_COLORS.text,
          font: CHART_FONT,
          callback: (v) => `${v}%`,
        },
      },
      y: {
        grid: { display: false },
        border: { display: false },
        ticks: { color: CHART_COLORS.text, font: CHART_FONT },
      },
    },
  };
}

function renderHeadToHeadChart() {
  const canvas = document.getElementById("chart-head-to-head");
  if (!canvas || !window.Chart) return;

  new Chart(canvas, {
    type: "bar",
    data: {
      labels: ["gin", "serde", "gson", "jekyll", "Slim", "guzzle", "zod"],
      datasets: [
        {
          label: "Aletheore",
          data: [80.0, 53.3, 40.0, 26.7, 26.7, 20.0, 20.0],
          backgroundColor: CHART_COLORS.accent,
          borderRadius: 4,
          barPercentage: 0.75,
          categoryPercentage: 0.7,
        },
        {
          label: "RepoWise (best mode)",
          data: [60.0, 13.3, 26.7, 13.3, 26.7, 20.0, 13.3],
          backgroundColor: CHART_COLORS.muted,
          borderRadius: 4,
          barPercentage: 0.75,
          categoryPercentage: 0.7,
        },
      ],
    },
    options: {
      ...baseBarOptions(90),
      plugins: {
        ...baseBarOptions(90).plugins,
        legend: {
          display: true,
          position: "top",
          align: "start",
          labels: {
            color: CHART_COLORS.text,
            font: CHART_FONT,
            boxWidth: 12,
            boxHeight: 12,
            usePointStyle: false,
          },
        },
      },
    },
  });
}

function renderSpreadChart() {
  const canvas = document.getElementById("chart-spread");
  if (!canvas || !window.Chart) return;

  new Chart(canvas, {
    type: "bar",
    data: {
      labels: [
        "gin", "flask", "serde", "jq", "gson", "fmt",
        "Slim", "jekyll", "zod", "axios", "AutoMapper", "thrift",
      ],
      datasets: [
        {
          label: "Top-1 (general phrasing)",
          data: [80.0, 71.9, 53.3, 53.3, 40.0, 40.0, 26.7, 26.7, 20.0, 20.0, 6.7, 6.7],
          backgroundColor: CHART_COLORS.accent,
          borderRadius: 4,
          barPercentage: 0.7,
          categoryPercentage: 0.75,
        },
      ],
    },
    options: baseBarOptions(90),
  });
}

renderHeadToHeadChart();
renderSpreadChart();
