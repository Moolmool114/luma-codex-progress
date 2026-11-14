/* Pure display formatting; estimates remain in seconds internally. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LumaFormat = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const valid = value => typeof value === "number" && Number.isFinite(value);
  function remaining(seconds) {
    if (!valid(seconds)) return "待估算";
    const minutes = Math.max(1, Math.ceil(Math.max(0, seconds) / 60));
    if (minutes < 60) return minutes + " 分钟";
    const hours = Math.floor(minutes / 60), tail = minutes % 60;
    return hours + " 小时" + (tail ? " " + tail + " 分" : "");
  }
  function elapsed(seconds) {
    if (!valid(seconds)) return "—";
    const n = Math.floor(Math.max(0, seconds)), pad = n => String(n).padStart(2, "0");
    const parts = [pad(Math.floor(n % 3600 / 60)), pad(n % 60)];
    if (n >= 3600) parts.unshift(pad(Math.floor(n / 3600)));
    return parts.join(":");
  }
  function clock(timestamp, now = Date.now() / 1000) {
    if (!valid(timestamp)) return "—";
    const date = new Date(timestamp * 1000), today = new Date(now * 1000);
    const days = Math.round((new Date(date.getFullYear(), date.getMonth(), date.getDate()) - new Date(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000);
    const prefix = days === 1 ? "明天 " : days > 1 ? (date.getMonth() + 1) + "/" + date.getDate() + " " : "";
    return prefix + String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
  }
  function range(values) {
    if (!Array.isArray(values) || values.length < 2) return "尚无可用区间";
    const a = remaining(values[0]), b = remaining(values[1]);
    if (a === b) return a;
    if (values[1] < 3600) return Math.max(1, Math.ceil(values[0] / 60)) + "–" + Math.max(1, Math.ceil(values[1] / 60)) + " 分钟";
    return a + " – " + b;
  }
  function age(timestamp, now) {
    const n = Math.max(0, now - timestamp);
    return n < 20 ? "刚刚" : n < 60 ? Math.floor(n) + " 秒前" : n < 3600 ? Math.floor(n / 60) + " 分钟前" : Math.floor(n / 3600) + " 小时前";
  }
  function span(seconds) {
    if (!valid(seconds)) return "—";
    const value = Math.max(0, Math.round(seconds));
    const hours = Math.floor(value/3600), minutes = Math.floor(value%3600/60), tail = value%60;
    if (hours) return hours + " 小时" + (minutes ? " " + minutes + " 分" : "");
    if (minutes) return minutes + " 分" + (tail ? " " + tail + " 秒" : "钟");
    return value + " 秒";
  }
  return {remaining, elapsed, clock, range, age, span};
});
