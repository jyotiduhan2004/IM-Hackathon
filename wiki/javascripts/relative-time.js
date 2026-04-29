(function () {
  let refreshIntervalId = null;

  function formatAbsoluteDate(date) {
    return date.toLocaleDateString("en-IN", {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "Asia/Kolkata",
    });
  }

  function formatAbsoluteDateTime(date) {
    return date.toLocaleString("en-IN", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZone: "Asia/Kolkata",
      timeZoneName: "short",
    });
  }

  function formatRelativeTime(iso) {
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) {
      return "";
    }

    const diffSeconds = Math.floor((Date.now() - then.getTime()) / 1000);
    const absSeconds = Math.abs(diffSeconds);
    const rtf = new Intl.RelativeTimeFormat("en-IN", { numeric: "auto" });
    const relativeValue = (unitSeconds) => {
      if (diffSeconds >= 0) {
        return -Math.floor(diffSeconds / unitSeconds);
      }
      return Math.floor(Math.abs(diffSeconds) / unitSeconds);
    };

    if (absSeconds < 60) {
      return "just now";
    }
    if (absSeconds < 3600) {
      return rtf.format(relativeValue(60), "minute");
    }
    if (absSeconds < 86400) {
      return rtf.format(relativeValue(3600), "hour");
    }
    if (absSeconds < 86400 * 30) {
      return rtf.format(relativeValue(86400), "day");
    }
    return formatAbsoluteDate(then);
  }

  function refreshRelativeTimes() {
    document.querySelectorAll("time.relative-time").forEach((el) => {
      const iso = el.getAttribute("datetime");
      if (!iso) {
        return;
      }
      const date = new Date(iso);
      const label = formatRelativeTime(iso);
      if (!label) {
        return;
      }
      el.textContent = label;
      el.title = formatAbsoluteDateTime(date);
    });
  }

  function startRefreshTimer() {
    if (refreshIntervalId !== null) {
      return;
    }
    refreshIntervalId = window.setInterval(refreshRelativeTimes, 60000);
  }

  if (typeof document$ !== "undefined") {
    document$.subscribe(() => {
      refreshRelativeTimes();
      startRefreshTimer();
    });
  } else {
    document.addEventListener("DOMContentLoaded", () => {
      refreshRelativeTimes();
      startRefreshTimer();
    });
  }
})();
