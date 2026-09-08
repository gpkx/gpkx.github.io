/**
 * 波幅探长 - 数据看板（整合版）
 * - 通用表：免费 TOP3 + VIP 全量
 * - 列轮转：最新采集列固定在最右侧（弹匣式），无新数据则保持
 * - 手机端仅展示最新 2 列，自动滚到最右
 * - 打赏入口（后台 tip_enabled）
 * js/components/index/Dashboard.js
 * DASHBOARD_BUILD 2026-09-08 rotating columns + mobile 2-col + no history expand
 */
import { store } from "../../store.js";
import { etfApi } from "../../api/etf.js";
import { request } from "../../api/http.js";

const dashboardPrefsApi = {
  fetch: () => request("/api/user/dashboard-prefs"),
  toggleFavorite: (etfCode) =>
    request("/api/user/dashboard-prefs", {
      method: "POST",
      body: JSON.stringify({ action: "toggle_favorite", etf_code: etfCode }),
    }),
  saveOrder: (order, favorites) =>
    request("/api/user/dashboard-prefs", {
      method: "POST",
      body: JSON.stringify({ order, favorites }),
    }),
};
import { CONFIG } from "../../config.js";

const { ref, computed, onMounted, nextTick, watch } = Vue;

function settingOn(val) {
  return val === "1" || val === 1 || val === true || val === "true";
}

/** 基准列序：周一..周五、周线（0..4 day，5 week） */
const BASE_COLS = [
  { key: "d0", type: "day", dayIdx: 0, label: "周一" },
  { key: "d1", type: "day", dayIdx: 1, label: "周二" },
  { key: "d2", type: "day", dayIdx: 2, label: "周三" },
  { key: "d3", type: "day", dayIdx: 3, label: "周四" },
  { key: "d4", type: "day", dayIdx: 4, label: "周五" },
  { key: "week", type: "week", dayIdx: -1, label: "周线" },
];

export default {
  name: "Dashboard",
  setup() {
    const loading = ref(false);
    const allData = ref([]);
    const chartsMap = ref({});
    /** 图表统一采集日 YYYY-MM-DD（北京）；仅来自 updated_at 或 R2 Last-Modified，绝不使用「今天」凑数 */
    const globalChartDay = ref(null);
    /** 周线图表采集日（与日线独立） */
    const weeklyChartDay = ref(null);
    const sharedList = ref([]); // 通用监控全量（无论是否触发）
    /** 会员收藏 / 自定义排序 */
    const favCodes = ref([]); // string codes，避免部分 WebView 对 Set 响应式异常
    const userOrder = ref([]);
    const dragCode = ref(null);
    const prefsSaving = ref(false);

    const searchQuery = ref("");
    const sortColumn = ref(null);
    const sortOrder = ref("desc");

    const tipVisible = ref(false);
    const tipChannel = ref("wechat"); // wechat | alipay
    const tableScrollEl = ref(null);

    /** 是否窄屏（手机）：仅展示最新 2 列 */
    const isMobile = ref(
      typeof window !== "undefined" && window.matchMedia
        ? window.matchMedia("(max-width: 640px)").matches
        : false
    );
    const updateIsMobile = () => {
      if (typeof window === "undefined" || !window.matchMedia) return;
      isMobile.value = window.matchMedia("(max-width: 640px)").matches;
    };

    /**
     * 手机端：横向滚到最右（最新列贴名称列右侧可见区）
     * 桌面：滚到最新列（最后一列）
     */
    const scrollToLatestCol = async () => {
      await nextTick();
      const el = tableScrollEl.value;
      if (!el) return;
      try {
        // 最新列在最右侧，直接滚到尽头
        el.scrollTo({ left: el.scrollWidth, behavior: "smooth" });
      } catch (_) {}
    };

    const settings = computed(() => store.state.publicSettings || {});
    const tipEnabled = computed(() => settingOn(settings.value.tip_enabled));

    const isImageUrl = (url) => {
      const u = String(url || "").trim();
      if (!u || !/^https?:\/\//i.test(u)) return false;
      return /\.(png|jpe?g|gif|webp|bmp|svg)(\?|#|$)/i.test(u);
    };
    const tipWechatSrc = computed(() => {
      const u = String(
        settings.value.wechat_qr_url || settings.value.tip_wechat_qr_url || ""
      ).trim();
      return isImageUrl(u) ? u : "";
    });
    const tipAlipaySrc = computed(() => {
      const u = String(
        settings.value.alipay_qr_url || settings.value.tip_alipay_qr_url || ""
      ).trim();
      return isImageUrl(u) ? u : "";
    });

    const isValidDate = (d) =>
      d && typeof d === "string" && /^\d{4}[-/]\d{1,2}[-/]\d{1,2}$/.test(d.trim());

    const parseYMD = (s) =>
      isValidDate(s) ? s.trim().split(/[-/]/).map((v) => parseInt(v, 10)) : [0, 0, 0];

    const formatDateCN = (dateStr) => {
      if (!dateStr || !isValidDate(dateStr)) return "";
      const [, m, d] = parseYMD(dateStr);
      return `${m}月${d}日`;
    };

    /** 标的名称：去掉「ETF」后面的文字（如 深证100ETF易方达 → 深证100ETF） */
    const formatEtfName = (name) => {
      if (!name) return "";
      const s = String(name).trim();
      const m = s.match(/^(.*?ETF)/i);
      return m ? m[1] : s;
    };

    /** 看板单元格：上午/下午|日线 */
    const formatDayCell = (item) => {
      if (!item) return "-";
      const am = item.am_status && item.am_status !== "--" ? item.am_status : "-";
      const pm = item.pm_status && item.pm_status !== "--" ? item.pm_status : "-";
      const day = item.day_status && item.day_status !== "--" ? item.day_status : "-";
      if (am === "-" && pm === "-" && day === "-") return "-";
      return am + "/" + pm + "|" + day;
    };

    const chartDateTitle = (dateStr) => {
      const cn = formatDateCN(dateStr);
      return cn ? cn + "图表" : "图表";
    };

    const dataDateTitle = (dateStr, kind = "") => {
      const cn = formatDateCN(dateStr);
      if (!cn) return kind || "";
      return kind ? cn + kind : cn;
    };

    const weekDataTitle = (item) => {
      if (!item || !item.week_status) return "";
      const cn = formatDateCN(item.week_status_date);
      return cn ? cn + "周线" : "周线";
    };

    const dailyChartTitle = (etfCode, colDate) => {
      const d = chartUpdateDay(etfCode) || globalChartDay.value || colDate;
      return chartDateTitle(d);
    };

    const weekChartTitle = () => {
      return chartDateTitle(weeklyChartDay.value || globalChartDay.value) || "周线图表";
    };

    const cellPrimaryStatus = (item) => {
      if (!item) return null;
      if (item.day_status && item.day_status !== "-" && item.day_status !== "--")
        return item.day_status;
      if (item.pm_status && item.pm_status !== "-" && item.pm_status !== "--")
        return item.pm_status;
      if (item.am_status && item.am_status !== "-" && item.am_status !== "--")
        return item.am_status;
      return null;
    };

    const getWeekDays = (dateStr) => {
      const [y, m, d] = parseYMD(dateStr);
      if (!y) return [];
      const dateObj = new Date(y, m - 1, d);
      const day = dateObj.getDay();
      const offset = day === 0 ? -6 : 1 - day;
      const monday = new Date(y, m - 1, d + offset);
      const days = [];
      for (let i = 0; i < 5; i++) {
        const temp = new Date(
          monday.getFullYear(),
          monday.getMonth(),
          monday.getDate() + i
        );
        days.push(
          `${temp.getFullYear()}-${String(temp.getMonth() + 1).padStart(2, "0")}-${String(
            temp.getDate()
          ).padStart(2, "0")}`
        );
      }
      return days;
    };

    const shiftMonday = (mondayStr, weeksBack) => {
      const [y, m, d] = parseYMD(mondayStr);
      if (!y) return "";
      const dt = new Date(y, m - 1, d - weeksBack * 7);
      return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(
        dt.getDate()
      ).padStart(2, "0")}`;
    };

    const getStatusVal = (str) => {
      if (!str || typeof str !== "string" || str === "-" || str === "--") return -9999;
      const match = str.match(/[-+]?[0-9]*\.?[0-9]+/);
      return match ? parseFloat(match[0]) : -9999;
    };

    /** 本周一（本地日历），行情为空时仍能展示监控列表 */
    const calendarMonday = () => {
      const d = new Date();
      const day = d.getDay(); // 0=Sun
      const diff = day === 0 ? -6 : 1 - day;
      d.setDate(d.getDate() + diff);
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      const dd = String(d.getDate()).padStart(2, "0");
      return `${y}-${m}-${dd}`;
    };

    const latestMonday = computed(() => {
      const validDates = [
        ...new Set(
          allData.value
            .filter((i) => i.date && isValidDate(i.date))
            .map((i) => i.date)
        ),
      ].sort();
      if (validDates.length) {
        const wDays = getWeekDays(validDates[validDates.length - 1]);
        if (wDays.length) return wDays[0];
      }
      return calendarMonday();
    });

    const R2_CHART_BASE = "https://pub-973330e118204686a625fe51431d4336.r2.dev/charts";

    /** 北京日历 YYYY-MM-DD */
    const bjYmd = (ms = Date.now()) => {
      try {
        return new Intl.DateTimeFormat("en-CA", {
          timeZone: "Asia/Shanghai",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        }).format(new Date(ms));
      } catch (_) {
        return new Date(ms).toISOString().slice(0, 10);
      }
    };

    const toBjDay = (val) => {
      if (val == null || val === "") return null;
      if (typeof val === "string" && /^\d{4}-\d{2}-\d{2}$/.test(val.trim()))
        return val.trim();
      let ts = Number(val);
      if (!ts || isNaN(ts)) {
        const parsed = Date.parse(String(val));
        if (isNaN(parsed)) return null;
        ts = parsed;
      }
      if (ts < 1e12) ts *= 1000;
      return bjYmd(ts);
    };

    /** 北京最近交易日：若今天是工作日用今天，否则往前找（周末用周五） */
    const latestTradingDayBj = () => {
      for (let i = 0; i <= 7; i++) {
        const ms = Date.now() - i * 24 * 3600 * 1000;
        const day = bjYmd(ms);
        const wd = new Date(day + "T12:00:00+08:00").getDay();
        if (wd !== 0 && wd !== 6) return day;
      }
      return bjYmd(Date.now());
    };

    const resolveChartEntry = (code) => {
      if (code == null) return null;
      const rawCode = String(code);
      const key6 = rawCode.replace(/\D/g, "").slice(-6) || rawCode;
      const map = chartsMap.value || {};
      const raw = map[key6] || map[rawCode] || map[code];
      if (!raw) return null;
      if (typeof raw === "string") return { url: raw, updated_at: null };
      return {
        url: raw.chart_url || raw.url || "",
        updated_at: raw.updated_at || raw.last_modified || null,
      };
    };

    const chartUpdateDay = (_code) => globalChartDay.value || null;

    const chartColIndexForCode = (etfCode) => {
      if (!latestMonday.value) return -1;
      const weekDays = getWeekDays(latestMonday.value);
      if (!weekDays.length) return -1;
      const day = chartUpdateDay(etfCode);
      if (!day) return -1;
      const idx = weekDays.indexOf(day);
      if (idx >= 0) return idx;
      return -1;
    };

    const hasChartForCode = (etfCode) => {
      const e = resolveChartEntry(etfCode);
      return !!(e && e.url);
    };

    /** 采集日对应列才显示；有采集日就显示（不依赖 charts 是否非空） */
    const showDailyChartIcon = (etfCode, colIdx) => {
      if (colIdx < 0) return false;
      let target = chartColIndexForCode(etfCode);
      // 周末不更新日线图：采集日落在周末时挂到本周周五列
      if (target < 0 && globalChartDay.value && latestMonday.value) {
        const weekDays = getWeekDays(latestMonday.value);
        const day = globalChartDay.value;
        if (day && weekDays.length) {
          const wd = new Date(day + "T12:00:00+08:00").getDay();
          if (wd === 0 || wd === 6) {
            target = 4; // 周五
          }
        }
      }
      return target === colIdx && target >= 0;
    };

    const resolveGlobalChartDay = async (sampleCodes = [], apiChartDate = null) => {
      const fromApi = toBjDay(apiChartDate);
      if (fromApi) {
        globalChartDay.value = fromApi;
        return fromApi;
      }
      let maxTs = 0;
      Object.values(chartsMap.value || {}).forEach((raw) => {
        if (!raw || typeof raw === "string") return;
        const u = raw.updated_at || raw.last_modified;
        if (u == null || u === "") return;
        let ts = Number(u);
        if (!ts || isNaN(ts)) ts = Date.parse(String(u));
        else if (ts < 1e12) ts *= 1000;
        if (ts && !isNaN(ts) && ts > maxTs) maxTs = ts;
      });
      if (maxTs > 0) {
        globalChartDay.value = bjYmd(maxTs);
        return globalChartDay.value;
      }
      globalChartDay.value = latestTradingDayBj();
      return globalChartDay.value;
    };

    const resolveWeeklyChartDay = async (
      apiWeeklyChartDate = null,
      apiChartDate = null
    ) => {
      const fromWeekly = toBjDay(apiWeeklyChartDate);
      if (fromWeekly) {
        weeklyChartDay.value = fromWeekly;
        return fromWeekly;
      }
      const fromDaily = toBjDay(apiChartDate) || globalChartDay.value;
      if (fromDaily) {
        weeklyChartDay.value = fromDaily;
        return fromDaily;
      }
      weeklyChartDay.value = latestTradingDayBj();
      return weeklyChartDay.value;
    };

    const handleSort = (column) => {
      if (sortColumn.value === column) {
        if (sortOrder.value === "desc") sortOrder.value = "asc";
        else {
          sortColumn.value = null;
          sortOrder.value = "desc";
        }
      } else {
        sortColumn.value = column;
        sortOrder.value = "desc";
      }
    };

    const isBlankStatus = (s) =>
      !s || s === "-" || s === "--" || s === "None" || s === "null";

    /**
     * 取某标的「指定周（周一键）」内最新一条 week_status（含该周周六日写入的记录）。
     */
    const findWeekStatusForMonday = (etfCode, mondayStr) => {
      if (!mondayStr) return null;
      let best = null;
      let bestDate = "";
      for (const item of allData.value) {
        if (String(item.etf_code) !== String(etfCode)) continue;
        if (!item.date || !isValidDate(item.date)) continue;
        if (isBlankStatus(item.week_status)) continue;
        const wDays = getWeekDays(item.date);
        if (!wDays.length || wDays[0] !== mondayStr) continue;
        if (!bestDate || item.date >= bestDate) {
          bestDate = item.date;
          best = String(item.week_status).trim();
        }
      }
      return best ? { status: best, date: bestDate } : null;
    };

    const processedData = computed(() => {
      const empty = {
        list: [],
        freeTop3Codes: [],
        weekDays: [],
        weekStatusMonday: "",
        rankBy: "daily",
        rankDailyIdx: -1,
        /** 弹匣列序（最右 = 最新） */
        displayCols: BASE_COLS.slice(),
        /** 手机端可见列（最新 2 列） */
        mobileCols: BASE_COLS.slice(-2),
        latestColKey: "week",
      };
      if (!latestMonday.value) return empty;

      const weekDays = getWeekDays(latestMonday.value);
      if (weekDays.length < 5) return empty;

      const etfMap = {};
      // ① 本周一～五行情格子
      allData.value.forEach((item) => {
        if (!item.date || !isValidDate(item.date)) return;
        const code =
          String(item.etf_code || "")
            .replace(/\D/g, "")
            .slice(-6) || item.etf_code;
        const idx = weekDays.indexOf(item.date);
        if (idx === -1) return;
        if (!etfMap[code]) {
          etfMap[code] = {
            etf_code: code,
            etf_name: item.etf_name,
            days: [null, null, null, null, null],
            week_status: null,
            week_status_date: null,
            week_status_from: null,
          };
        }
        etfMap[code].days[idx] = item;
        if (item.etf_name) etfMap[code].etf_name = item.etf_name;
      });

      // ② 并入通用监控列表
      (sharedList.value || []).forEach((s) => {
        const code = String(s.etf_code || s.code || "")
          .replace(/\D/g, "")
          .slice(-6);
        if (code.length !== 6) return;
        if (!etfMap[code]) {
          etfMap[code] = {
            etf_code: code,
            etf_name: s.etf_name || s.name || code,
            days: [null, null, null, null, null],
            week_status: null,
            week_status_date: null,
            week_status_from: null,
          };
        } else if ((s.etf_name || s.name) && !etfMap[code].etf_name) {
          etfMap[code].etf_name = s.etf_name || s.name;
        }
      });

      // ③ 行情里出现过但不在本周格子的代码
      allData.value.forEach((item) => {
        const code = String(item.etf_code || "")
          .replace(/\D/g, "")
          .slice(-6);
        if (code.length !== 6) return;
        if (!etfMap[code]) {
          etfMap[code] = {
            etf_code: code,
            etf_name: item.etf_name || code,
            days: [null, null, null, null, null],
            week_status: null,
            week_status_date: null,
            week_status_from: null,
          };
        }
      });

      // ④ 周线：主表只展示「当前展示周」最新一条；没有则「-」
      Object.values(etfMap).forEach((row) => {
        const cur = findWeekStatusForMonday(row.etf_code, latestMonday.value);
        if (cur) {
          row.week_status = cur.status;
          row.week_status_date = cur.date;
          row.week_status_from = "current";
        } else {
          row.week_status = null;
          row.week_status_date = null;
          row.week_status_from = null;
        }
      });

      const weekStatusMonday = latestMonday.value;
      let items = Object.values(etfMap);

      const hasStatus = (s) => !!(s && s !== "-" && s !== "--");
      const cellHasDay = (row, idx) => hasStatus(row.days?.[idx]?.day_status);
      const cellHasHalf = (row, idx) => {
        const c = row.days?.[idx];
        return !!(c && (hasStatus(c.am_status) || hasStatus(c.pm_status)));
      };
      const cellHasAny = (row, idx) => cellHasDay(row, idx) || cellHasHalf(row, idx);

      // 最新有行情数据的交易日列
      let latestIdx = -1;
      for (let idx = 4; idx >= 0; idx--) {
        if (items.some((i) => cellHasAny(i, idx))) {
          latestIdx = idx;
          break;
        }
      }

      // 免费 Top3：仅看「最新有日线」的那一列
      let dailyColIdx = -1;
      for (let idx = 4; idx >= 0; idx--) {
        if (items.some((i) => cellHasDay(i, idx))) {
          dailyColIdx = idx;
          break;
        }
      }

      const hasAnyWeek = items.some((i) => hasStatus(i.week_status));

      const todayBj = bjYmd(Date.now());
      let isWeekendBj = false;
      try {
        const wd = new Date(todayBj + "T12:00:00+08:00").getDay();
        isWeekendBj = wd === 0 || wd === 6;
      } catch (_) {}

      let maxWeekStatusDate = "";
      for (const row of items) {
        if (!hasStatus(row.week_status)) continue;
        const d = row.week_status_date;
        if (d && isValidDate(d) && d > maxWeekStatusDate) maxWeekStatusDate = d;
      }
      const weeklyCollectDay =
        (weeklyChartDay.value &&
          isValidDate(weeklyChartDay.value) &&
          weeklyChartDay.value) ||
        maxWeekStatusDate ||
        "";
      const dailyCollectDay =
        (globalChartDay.value &&
          isValidDate(globalChartDay.value) &&
          globalChartDay.value) ||
        (latestIdx >= 0 && weekDays[latestIdx] ? weekDays[latestIdx] : "") ||
        "";

      // 采集日谁新谁优先；周末跑完周线任务 → 强制周线
      let rankBy = "daily";
      if (hasAnyWeek && isWeekendBj) {
        rankBy = "weekly";
      } else if (
        hasAnyWeek &&
        weeklyCollectDay &&
        dailyCollectDay &&
        weeklyCollectDay > dailyCollectDay
      ) {
        rankBy = "weekly";
      } else if (hasAnyWeek && weeklyCollectDay && weeklyCollectDay === todayBj) {
        rankBy = "weekly";
      } else if (latestIdx >= 0) {
        rankBy = "daily";
      } else if (hasAnyWeek) {
        rankBy = "weekly";
      }

      /**
       * 弹匣轮转：最右 = 最新列
       * 例：最新是周一(d0) → [周二,周三,周四,周五,周线,周一]
       * 最新是周线 → [周一..周五,周线]
       */
      let pivotIdx = 5; // 默认周线
      if (rankBy === "weekly") {
        pivotIdx = 5;
      } else if (latestIdx >= 0) {
        pivotIdx = latestIdx; // 0..4
      } else if (globalChartDay.value && weekDays.length) {
        const ci = weekDays.indexOf(globalChartDay.value);
        if (ci >= 0) pivotIdx = ci;
        else {
          const wd = new Date(
            globalChartDay.value + "T12:00:00+08:00"
          ).getDay();
          if (wd === 0 || wd === 6) pivotIdx = 4;
        }
      }

      // 轮转：从 pivot 的下一列开始，到 pivot 结束（pivot 在最右）
      const n = BASE_COLS.length;
      const displayCols = [];
      for (let i = 1; i <= n; i++) {
        displayCols.push(BASE_COLS[(pivotIdx + i) % n]);
      }
      const mobileCols = displayCols.slice(-2);
      const latestColKey = displayCols[displayCols.length - 1].key;

      const absDayVal = (row, dayIdx) => {
        if (dayIdx == null || dayIdx < 0) return -9999;
        const s = row.days?.[dayIdx]?.day_status;
        if (!hasStatus(s)) return -9999;
        const v = getStatusVal(s);
        return v === -9999 ? -9999 : Math.abs(v);
      };

      const absHalfVal = (row, dayIdx) => {
        if (dayIdx == null || dayIdx < 0) return -9999;
        const item = row.days?.[dayIdx];
        if (!item) return -9999;
        const pm = getStatusVal(item.pm_status);
        const am = getStatusVal(item.am_status);
        let best = -9999;
        if (pm !== -9999) best = Math.max(best, Math.abs(pm));
        if (am !== -9999) best = Math.max(best, Math.abs(am));
        return best;
      };

      const absWeekVal = (row) => {
        const s = row.week_status;
        if (!hasStatus(s)) return -9999;
        const v = getStatusVal(s);
        return v === -9999 ? -9999 : Math.abs(v);
      };

      const cmpDayColumn = (a, b, idx, orderDesc = true) => {
        const da = absDayVal(a, idx);
        const db = absDayVal(b, idx);
        if (da !== db) {
          if (da === -9999) return 1;
          if (db === -9999) return -1;
          return orderDesc ? db - da : da - db;
        }
        const ha = absHalfVal(a, idx);
        const hb = absHalfVal(b, idx);
        if (ha !== hb) {
          if (ha === -9999) return 1;
          if (hb === -9999) return -1;
          return orderDesc ? hb - ha : ha - hb;
        }
        return String(a.etf_code || "").localeCompare(String(b.etf_code || ""));
      };

      const cmpWeekColumn = (a, b, orderDesc = true) => {
        const wa = absWeekVal(a);
        const wb = absWeekVal(b);
        if (wa !== wb) {
          if (wa === -9999) return 1;
          if (wb === -9999) return -1;
          return orderDesc ? wb - wa : wa - wb;
        }
        return String(a.etf_code || "").localeCompare(String(b.etf_code || ""));
      };

      /** 默认排序 = 按最右列（最新列） */
      const cmpDefaultRank = (a, b) => {
        const last = displayCols[displayCols.length - 1];
        if (last.type === "week") return cmpWeekColumn(a, b, true);
        return cmpDayColumn(a, b, last.dayIdx, true);
      };

      // 免费看图 TOP3：与当前默认排序列一致
      const freeTopN = 3;
      let freeTop3Codes = [];
      if (rankBy === "weekly") {
        freeTop3Codes = [...items]
          .filter((i) => absWeekVal(i) > -9999)
          .sort((a, b) => {
            const d = absWeekVal(b) - absWeekVal(a);
            if (d !== 0) return d;
            return String(a.etf_code || "").localeCompare(String(b.etf_code || ""));
          })
          .slice(0, freeTopN)
          .map((i) => i.etf_code);
      } else if (dailyColIdx >= 0) {
        freeTop3Codes = [...items]
          .filter((i) => absDayVal(i, dailyColIdx) > -9999)
          .sort((a, b) => {
            const d = absDayVal(b, dailyColIdx) - absDayVal(a, dailyColIdx);
            if (d !== 0) return d;
            return String(a.etf_code || "").localeCompare(String(b.etf_code || ""));
          })
          .slice(0, freeTopN)
          .map((i) => i.etf_code);
      }

      items.sort((a, b) => {
        if (sortColumn.value) {
          if (sortColumn.value === "etf_name") {
            const cmp = (a.etf_name || "").localeCompare(b.etf_name || "", "zh-CN");
            return sortOrder.value === "asc" ? cmp : -cmp;
          }
          if (sortColumn.value.startsWith("d")) {
            const idx = parseInt(sortColumn.value.substring(1), 10);
            return cmpDayColumn(a, b, idx, sortOrder.value !== "asc");
          }
          if (sortColumn.value === "week_status" || sortColumn.value === "week") {
            return cmpWeekColumn(a, b, sortOrder.value !== "asc");
          }
        }
        return cmpDefaultRank(a, b);
      });

      if (searchQuery.value) {
        const q = searchQuery.value.toLowerCase().trim();
        items = items.filter(
          (i) =>
            (i.etf_name && i.etf_name.toLowerCase().includes(q)) ||
            (i.etf_code && i.etf_code.toLowerCase().includes(q))
        );
      }

      // 默认排序结构（未手动点列时）
      if (!sortColumn.value) {
        if (rankBy === "weekly") {
          items.sort((a, b) => cmpWeekColumn(a, b, true));
        } else {
          const freeSet = new Set((freeTop3Codes || []).map((c) => String(c)));
          const freeIdx = new Map(
            (freeTop3Codes || []).map((c, i) => [String(c), i])
          );
          const favSet = new Set(
            store.state.isLoggedIn && store.state.isVip
              ? (Array.isArray(favCodes.value) ? favCodes.value : []).map((c) =>
                  String(c)
                )
              : []
          );
          const orderMap = new Map(
            (Array.isArray(userOrder.value) ? userOrder.value : []).map((c, i) => [
              String(c),
              i,
            ])
          );
          const groupOf = (code) => {
            const c = String(code);
            if (freeSet.has(c)) return 0;
            if (favSet.has(c)) return 1;
            return 2;
          };
          items.sort((a, b) => {
            const ca = String(a.etf_code);
            const cb = String(b.etf_code);
            const ga = groupOf(ca);
            const gb = groupOf(cb);
            if (ga !== gb) return ga - gb;
            if (ga === 0) {
              return (freeIdx.get(ca) ?? 0) - (freeIdx.get(cb) ?? 0);
            }
            const primary = cmpDefaultRank(a, b);
            if (primary !== 0) return primary;
            if (orderMap.size) {
              const ia = orderMap.has(ca) ? orderMap.get(ca) : 100000;
              const ib = orderMap.has(cb) ? orderMap.get(cb) : 100000;
              if (ia !== ib) return ia - ib;
            }
            return 0;
          });
        }
      }

      return {
        list: items,
        freeTop3Codes,
        weekDays,
        weekStatusMonday,
        rankBy,
        rankDailyIdx: rankBy === "daily" ? latestIdx : -1,
        displayCols,
        mobileCols,
        latestColKey,
      };
    });

    /** 当前应渲染的列（桌面全量轮转 / 手机最新 2 列） */
    const visibleCols = computed(() => {
      const pd = processedData.value;
      if (!pd || !pd.displayCols) return BASE_COLS;
      return isMobile.value ? pd.mobileCols || pd.displayCols.slice(-2) : pd.displayCols;
    });

    const canViewChart = (etfCode) => {
      if (store.state.isVip) return true;
      return processedData.value.freeTop3Codes.includes(etfCode);
    };

    const probeImage = (url) =>
      new Promise((resolve) => {
        const img = new Image();
        img.onload = () => resolve(true);
        img.onerror = () => resolve(false);
        img.src = url;
      });

    const ensureViewerNavStyle = () => {
      if (document.getElementById("bofutz-viewer-nav-style")) return;
      const style = document.createElement("style");
      style.id = "bofutz-viewer-nav-style";
      style.textContent = `
        .bofutz-viewer-nav {
          position: absolute;
          top: 50%;
          transform: translateY(-50%);
          z-index: 30;
          width: 52px;
          height: 52px;
          border-radius: 999px;
          border: 2.5px solid rgba(255,255,255,0.92);
          background: rgba(15, 23, 42, 0.45);
          color: #fff;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          box-shadow: 0 6px 20px rgba(0,0,0,.28);
          -webkit-tap-highlight-color: transparent;
          user-select: none;
          backdrop-filter: blur(6px);
          transition: background .15s ease, transform .15s ease, border-color .15s ease;
          padding: 0;
        }
        .bofutz-viewer-nav:hover {
          background: rgba(15, 23, 42, 0.7);
          border-color: #fff;
        }
        .bofutz-viewer-nav:active { transform: translateY(-50%) scale(0.94); }
        .bofutz-viewer-nav svg {
          width: 22px;
          height: 22px;
          display: block;
          fill: none;
          stroke: currentColor;
          stroke-width: 2.6;
          stroke-linecap: round;
          stroke-linejoin: round;
        }
        .bofutz-viewer-prev { left: 16px; }
        .bofutz-viewer-next { right: 16px; }
        @media (max-width: 640px) {
          .bofutz-viewer-nav { width: 46px; height: 46px; }
          .bofutz-viewer-nav svg { width: 20px; height: 20px; }
          .bofutz-viewer-prev { left: 8px; }
          .bofutz-viewer-next { right: 8px; }
        }
      `;
      document.head.appendChild(style);
    };

    const isFavorite = (code) => {
      const list = Array.isArray(favCodes.value) ? favCodes.value : [];
      const c = String(code || "")
        .replace(/\D/g, "")
        .slice(-6);
      return !!c && list.includes(c);
    };

    const canCustomizeBoard = computed(
      () => !!(store.state.isLoggedIn && store.state.isVip)
    );

    const loadDashboardPrefs = async () => {
      if (!canCustomizeBoard.value) {
        favCodes.value = [];
        userOrder.value = [];
        return;
      }
      try {
        const res = await dashboardPrefsApi.fetch();
        const data = (res && res.data) || res || {};
        favCodes.value = (data.favorites || [])
          .map((c) => String(c).replace(/\D/g, "").slice(-6))
          .filter((c) => c.length === 6);
        userOrder.value = (data.order || []).map((c) =>
          String(c).replace(/\D/g, "").slice(-6)
        );
      } catch (e) {
        console.log("dashboard prefs", e && e.message);
      }
    };

    const toggleFavorite = async (item, ev) => {
      if (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      }
      if (!canCustomizeBoard.value) {
        store.showToast("登录会员后可收藏标的", "error");
        return;
      }
      const code = String(item.etf_code || "")
        .replace(/\D/g, "")
        .slice(-6);
      if (code.length !== 6) return;
      try {
        const res = await dashboardPrefsApi.toggleFavorite(code);
        const on = !!(res && (res.favorite === true || res.favorite === 1));
        const cur = Array.isArray(favCodes.value) ? favCodes.value.slice() : [];
        const idx = cur.indexOf(code);
        if (on && idx < 0) cur.push(code);
        if (!on && idx >= 0) cur.splice(idx, 1);
        favCodes.value = cur;
        store.showToast(on ? "已收藏" : "已取消收藏");
      } catch (err) {
        store.showToast(err.message || "收藏失败", "error");
      }
    };

    const onDragStart = (item, ev) => {
      if (!canCustomizeBoard.value) {
        ev.preventDefault();
        return;
      }
      dragCode.value = String(item.etf_code);
      try {
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", String(item.etf_code));
      } catch (_) {}
    };

    const onDragOver = (ev) => {
      if (!canCustomizeBoard.value) return;
      ev.preventDefault();
      try {
        ev.dataTransfer.dropEffect = "move";
      } catch (_) {}
    };

    const onDropRow = async (targetItem, ev) => {
      if (!canCustomizeBoard.value) return;
      ev.preventDefault();
      ev.stopPropagation();
      const from =
        dragCode.value ||
        (ev.dataTransfer && ev.dataTransfer.getData("text/plain"));
      const to = String(targetItem.etf_code);
      dragCode.value = null;
      if (!from || from === to) return;

      const list = (processedData.value.list || []).map((x) => String(x.etf_code));
      const next = list.slice();
      const fi = next.indexOf(String(from));
      const ti = next.indexOf(to);
      if (fi < 0 || ti < 0) return;
      next.splice(fi, 1);
      next.splice(ti, 0, String(from));
      userOrder.value = next;
      if (prefsSaving.value) return;
      prefsSaving.value = true;
      try {
        await dashboardPrefsApi.saveOrder(next, Array.from(favCodes.value));
      } catch (err) {
        store.showToast(err.message || "排序保存失败", "error");
      } finally {
        prefsSaving.value = false;
      }
    };

    const showViewerWithMultiImages = (imgList, initialIndex = 0) => {
      if (!imgList || !imgList.length) return;
      const container = document.createElement("div");
      container.style.display = "none";
      imgList.forEach((item) => {
        const img = document.createElement("img");
        img.src = item.url;
        img.alt = item.title;
        container.appendChild(img);
      });
      document.body.appendChild(container);
      const isMulti = imgList.length > 1;
      if (window.Viewer) {
        ensureViewerNavStyle();
        let navPrev = null;
        let navNext = null;
        const clearNav = () => {
          try {
            navPrev && navPrev.remove();
            navNext && navNext.remove();
          } catch (_) {}
          navPrev = navNext = null;
        };
        const viewer = new window.Viewer(container, {
          hidden: () => {
            clearNav();
            viewer.destroy();
            container.remove();
          },
          title: true,
          navbar: isMulti,
          tooltip: true,
          movable: true,
          zoomable: true,
          rotatable: false,
          scalable: false,
          transition: true,
          keyboard: isMulti,
          loop: isMulti,
          initialViewIndex: Math.min(initialIndex, imgList.length - 1),
          toolbar: {
            zoomIn: 1,
            zoomOut: 1,
            oneToOne: 1,
            reset: 1,
            prev: isMulti ? 1 : 0,
            play: 0,
            next: isMulti ? 1 : 0,
            rotateLeft: 0,
            rotateRight: 0,
            flipHorizontal: 0,
            flipVertical: 0,
          },
          ready() {
            if (!isMulti) return;
            const root =
              (viewer && viewer.viewer) ||
              document.querySelector(".viewer-container");
            if (!root) return;
            if (getComputedStyle(root).position === "static") {
              root.style.position = "relative";
            }
            clearNav();
            navPrev = document.createElement("button");
            navPrev.type = "button";
            navPrev.className = "bofutz-viewer-nav bofutz-viewer-prev";
            navPrev.setAttribute("aria-label", "上一张");
            navPrev.innerHTML =
              '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="15 6 9 12 15 18"></polyline></svg>';
            navPrev.addEventListener("click", (e) => {
              e.preventDefault();
              e.stopPropagation();
              try {
                viewer.prev(true);
              } catch (_) {}
            });
            navNext = document.createElement("button");
            navNext.type = "button";
            navNext.className = "bofutz-viewer-nav bofutz-viewer-next";
            navNext.setAttribute("aria-label", "下一张");
            navNext.innerHTML =
              '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 6 15 12 9 18"></polyline></svg>';
            navNext.addEventListener("click", (e) => {
              e.preventDefault();
              e.stopPropagation();
              try {
                viewer.next(true);
              } catch (_) {}
            });
            root.appendChild(navPrev);
            root.appendChild(navNext);
          },
        });
        viewer.show();
      } else {
        window.open(imgList[initialIndex]?.url, "_blank");
      }
    };

    const viewableBoardItems = () => {
      const list = processedData.value?.list || [];
      return list.filter((row) => row && canViewChart(row.etf_code));
    };

    const probeImagesBatch = async (candidates, concurrency = 12) => {
      const out = [];
      let i = 0;
      const workers = Array.from(
        { length: Math.min(concurrency, Math.max(1, candidates.length)) },
        async () => {
          while (i < candidates.length) {
            const idx = i++;
            const c = candidates[idx];
            if (c && c.url && (await probeImage(c.url))) out.push({ ...c, _idx: idx });
          }
        }
      );
      await Promise.all(workers);
      out.sort((a, b) => a._idx - b._idx);
      return out.map(({ _idx, ...rest }) => rest);
    };

    const openDailyChartViewer = async (item) => {
      if (!canViewChart(item.etf_code)) {
        if (
          confirm("此为 VIP 专属图表 (免费标的除外)。\n是否去开通监控 VIP？")
        ) {
          window.location.hash = "#/plan";
        }
        return;
      }
      const rows = viewableBoardItems();
      if (!rows.length) {
        store.showToast("暂无可查看的图表", "error");
        return;
      }
      store.showToast("正在加载图库…");
      const dayLabel =
        formatDateCN(chartUpdateDay(item.etf_code) || globalChartDay.value) || "";
      const candidates = [];
      for (const row of rows) {
        const code =
          String(row.etf_code || "")
            .replace(/\D/g, "")
            .slice(-6) || row.etf_code;
        const name = formatEtfName(row.etf_name) || code;
        const entry = resolveChartEntry(code);
        const r2Daily = `https://pub-973330e118204686a625fe51431d4336.r2.dev/charts/${code}_daily.png`;
        const r2Half = `https://pub-973330e118204686a625fe51431d4336.r2.dev/charts/${code}_half_day.png`;
        const dailyUrl = (entry && entry.url) || r2Daily;
        const rowDayLabel =
          formatDateCN(chartUpdateDay(code) || globalChartDay.value) || dayLabel;
        candidates.push({
          title: `${name} (${code}) ${rowDayLabel}日线`.replace(/\s+/g, " ").trim(),
          url: dailyUrl,
          code,
          kind: "daily",
        });
        if (entry && entry.url && entry.url !== r2Daily) {
          candidates.push({
            title: `${name} (${code}) ${rowDayLabel}日线(R2)`.replace(/\s+/g, " ").trim(),
            url: r2Daily,
            code,
            kind: "daily_r2",
          });
        }
        candidates.push({
          title: `${name} (${code}) ${rowDayLabel}半日线`.replace(/\s+/g, " ").trim(),
          url: r2Half,
          code,
          kind: "half_day",
        });
      }
      const images = await probeImagesBatch(candidates, 12);
      if (!images.length) {
        store.showToast("暂无可用日线/半日线图表", "error");
        return;
      }
      const clickCode =
        String(item.etf_code || "")
          .replace(/\D/g, "")
          .slice(-6) || item.etf_code;
      let idx = images.findIndex((g) => g.code === clickCode && g.kind === "daily");
      if (idx < 0) idx = images.findIndex((g) => g.code === clickCode);
      if (idx < 0) idx = 0;
      showViewerWithMultiImages(images, idx);
    };

    const openWeeklyChartViewer = async (item) => {
      if (!canViewChart(item.etf_code)) {
        if (
          confirm("此为 VIP 专属图表 (免费标的除外)。\n是否去开通通用 VIP？")
        ) {
          window.location.hash = "#/plan";
        }
        return;
      }
      const rows = viewableBoardItems();
      if (!rows.length) {
        store.showToast("暂无可查看的图表", "error");
        return;
      }
      store.showToast("正在加载图库…");
      const candidates = [];
      for (const row of rows) {
        const code =
          String(row.etf_code || "")
            .replace(/\D/g, "")
            .slice(-6) || row.etf_code;
        const name = formatEtfName(row.etf_name) || code;
        const rowDayLabel =
          formatDateCN(
            weeklyChartDay.value || globalChartDay.value || row.week_status_date
          ) || "";
        candidates.push({
          title: `${name} (${code}) ${rowDayLabel}周线`.replace(/\s+/g, " ").trim(),
          url: `https://pub-973330e118204686a625fe51431d4336.r2.dev/charts/${code}_weekly.png`,
          code,
          kind: "weekly",
        });
      }
      const images = await probeImagesBatch(candidates, 12);
      if (!images.length) {
        store.showToast("暂无可用周线图表", "error");
        return;
      }
      const clickCode =
        String(item.etf_code || "")
          .replace(/\D/g, "")
          .slice(-6) || item.etf_code;
      let idx = images.findIndex((g) => g.code === clickCode);
      if (idx < 0) idx = 0;
      showViewerWithMultiImages(images, idx);
    };

    const getColorClass = (status) => {
      if (!status || status === "-" || status === "--") return "text-slate-300";
      return status.includes("+") ? "text-red-500" : "text-emerald-500";
    };

    const formatExpire = (ts) => {
      if (!ts) return "";
      const d = new Date(ts);
      if (isNaN(d.getTime())) return "";
      return `${d.getMonth() + 1}/${d.getDate()}到期`;
    };

    const initData = async () => {
      loading.value = true;
      try {
        const tasks = [
          etfApi.fetchEtfRawData().catch(() => []),
          etfApi.fetchChartsMap().catch(() => ({})),
          etfApi.fetchSharedWatchlist().catch(() => ({ data: [] })),
        ];
        const results = await Promise.all(tasks);
        try {
          if (store.state.isLoggedIn && store.state.isVip) {
            await loadDashboardPrefs();
          } else {
            favCodes.value = [];
            userOrder.value = [];
          }
        } catch (_) {
          favCodes.value = [];
          userOrder.value = [];
        }
        const data = results[0];
        const chartsRes = results[1];
        const sharedRes = results[2];
        if (Array.isArray(data)) allData.value = data;
        chartsMap.value = chartsRes.charts || chartsRes || {};
        const sharedRaw = sharedRes?.data ?? sharedRes;
        sharedList.value = Array.isArray(sharedRaw) ? sharedRaw : [];
        const sampleCodes = (sharedList.value || [])
          .map((s) => s.etf_code || s.code)
          .concat((allData.value || []).map((i) => i.etf_code));
        await resolveGlobalChartDay(sampleCodes, chartsRes && chartsRes.chart_date);
        await resolveWeeklyChartDay(
          chartsRes && (chartsRes.weekly_chart_date || chartsRes.week_chart_date),
          chartsRes && chartsRes.chart_date
        );
      } catch (err) {
        store.showToast(err.message, "error");
      } finally {
        loading.value = false;
        await scrollToLatestCol();
      }
    };

    onMounted(async () => {
      updateIsMobile();
      if (typeof window !== "undefined") {
        window.addEventListener("resize", updateIsMobile);
      }
      await initData();
      setTimeout(scrollToLatestCol, 120);
      setTimeout(scrollToLatestCol, 400);
    });

    watch(
      () => [
        processedData.value.rankDailyIdx,
        processedData.value.latestColKey,
        globalChartDay.value,
        loading.value,
        isMobile.value,
      ],
      () => {
        if (!loading.value) setTimeout(scrollToLatestCol, 80);
      }
    );

    return {
      loading,
      searchQuery,
      sortColumn,
      sortOrder,
      handleSort,
      processedData,
      visibleCols,
      isMobile,
      chartColIndexForCode,
      hasChartForCode,
      showDailyChartIcon,
      chartUpdateDay,
      globalChartDay,
      formatDateCN,
      formatEtfName,
      isFavorite,
      toggleFavorite,
      onDragStart,
      onDragOver,
      onDropRow,
      canCustomizeBoard,
      dragCode,

      formatDayCell,
      chartDateTitle,
      dataDateTitle,
      dailyChartTitle,
      weekDataTitle,
      weekChartTitle,
      weeklyChartDay,
      cellPrimaryStatus,
      formatExpire,
      openDailyChartViewer,
      openWeeklyChartViewer,
      getColorClass,
      tipEnabled,
      tipVisible,
      tipChannel,
      tableScrollEl,
      tipWechatSrc,
      tipAlipaySrc,
      settings,
      store: store.state,
    };
  },
  template: `
    <div class="max-w-7xl mx-auto space-y-3 sm:space-y-4 select-none">
      <div class="bg-white rounded-xl shadow-sm border border-slate-100 flex items-center w-full">
        <i class="fa-solid fa-magnifying-glass text-slate-400 pl-3.5"></i>
        <input v-model="searchQuery" type="search" placeholder="搜索 标的代码/名称..." class="w-full bg-transparent border-none outline-none text-sm py-2.5 px-3">
      </div>

      <div v-if="loading" class="text-center py-12 text-slate-400">
        <i class="fa-solid fa-spinner animate-spin text-2xl theme-text"></i>
        <p class="mt-2 text-sm">读取云端数据中...</p>
      </div>

      <template v-else>
        <div v-if="!processedData.list.length" class="text-center py-12 text-slate-400 bg-white rounded-xl border border-slate-100">
          <i class="fa-solid fa-folder-open text-4xl mb-3 opacity-40"></i>
          <p>暂无相关行情数据</p>
        </div>

        <div v-else class="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
          <div ref="tableScrollEl" class="overflow-x-auto custom-scrollbar dash-table-scroll">
            <table class="text-center border-collapse whitespace-nowrap w-full dash-board-table">
              <thead class="bg-slate-50 border-b border-slate-100">
                <tr class="text-xs text-slate-600 font-bold select-none">
                  <th class="py-3 px-2 sm:px-4 text-left etf-name-column dash-col-name sticky top-0 left-0 bg-slate-50 z-40 cursor-pointer hover:bg-slate-100 transition-colors border-b border-r border-slate-200 shadow-[2px_0_4px_-2px_rgba(0,0,0,0.08)]" @click="handleSort('etf_name')">
                    标的名称
                    <i v-if="sortColumn==='etf_name'" class="fa-solid text-[10px] ml-1" :class="sortOrder==='asc'?'fa-arrow-up':'fa-arrow-down'"></i>
                  </th>
                  <th v-for="col in visibleCols" :key="col.key"
                      class="py-3 px-1.5 sm:px-2 sticky top-0 bg-slate-50 z-30 cursor-pointer hover:bg-slate-100 transition-colors border-b border-slate-200"
                      :class="col.type==='week' ? 'dash-col-week' : 'dash-col-day'"
                      @click="handleSort(col.type==='week' ? 'week_status' : col.key)">
                    {{ col.label }}
                    <i v-if="sortColumn===(col.type==='week'?'week_status':col.key) || (!sortColumn && processedData.latestColKey===col.key)"
                       class="fa-solid text-[10px] ml-1"
                       :class="sortColumn===(col.type==='week'?'week_status':col.key) && sortOrder==='asc' ? 'fa-arrow-up' : 'fa-arrow-down'"></i>
                  </th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-50 text-sm">
                <tr v-for="item in processedData.list" :key="item.etf_code"
                    class="hover:bg-[#4da6a0]/5 transition-colors group"
                    :class="{ 'opacity-60': dragCode === item.etf_code }"
                    :draggable="canCustomizeBoard ? true : false"
                    @dragstart="onDragStart(item, $event)"
                    @dragover="onDragOver($event)"
                    @drop="onDropRow(item, $event)">
                  <td class="p-2 sm:p-3 text-left relative sticky left-0 bg-white group-hover:bg-[#f6faf9] z-10 etf-name-column dash-col-name border-r border-slate-100 shadow-[2px_0_4px_-2px_rgba(0,0,0,0.06)]">
                    <div v-if="processedData.freeTop3Codes.includes(item.etf_code)" class="absolute left-0 top-0 bottom-0 w-1 theme-bg"></div>
                    <div class="flex items-start gap-1 min-w-0">
                      <button type="button"
                              class="mt-0.5 shrink-0 p-0.5 leading-none"
                              :title="canCustomizeBoard ? (isFavorite(item.etf_code) ? '取消收藏' : '收藏') : '会员可收藏'"
                              @click="toggleFavorite(item, $event)">
                        <i class="fa-solid fa-star text-sm"
                           :class="isFavorite(item.etf_code) ? 'text-amber-400' : 'text-slate-300'"></i>
                      </button>
                      <div class="min-w-0 flex-1 overflow-hidden">
                        <div class="font-bold text-slate-800 group-hover:theme-text flex items-center gap-0.5 flex-nowrap">
                          <span v-if="canCustomizeBoard" class="text-slate-300 text-[10px] cursor-grab active:cursor-grabbing select-none shrink-0" title="拖动排序">⋮⋮</span>
                          <span class="truncate text-[12px] sm:text-sm leading-tight" :title="formatEtfName(item.etf_name)">{{ formatEtfName(item.etf_name) }}</span>
                          <span v-if="processedData.freeTop3Codes.includes(item.etf_code)" class="text-[9px] bg-orange-100 text-orange-600 px-1 py-0.2 rounded font-bold shrink-0">免费</span>
                        </div>
                        <div class="text-[11px] text-slate-400 font-mono">{{ item.etf_code }}</div>
                      </div>
                    </div>
                  </td>

                  <td v-for="col in visibleCols" :key="col.key"
                      class="p-1.5 sm:p-3 font-medium"
                      :class="[
                        col.type==='week' ? 'dash-col-week' : 'dash-col-day',
                        getColorClass(col.type==='week' ? item.week_status : cellPrimaryStatus(item.days[col.dayIdx]))
                      ]">
                    <template v-if="col.type==='day'">
                      <div class="dash-cell-inner flex flex-col sm:flex-row items-center justify-center gap-0.5 sm:gap-1"
                           :title="dataDateTitle(processedData.weekDays[col.dayIdx])">
                        <span class="text-[10px] sm:text-sm font-mono tracking-tight leading-tight">{{ formatDayCell(item.days[col.dayIdx]) }}</span>
                        <i v-if="showDailyChartIcon(item.etf_code, col.dayIdx)"
                           class="fa-regular fa-image text-slate-400 hover:text-blue-500 cursor-pointer text-sm sm:text-xs shrink-0 p-1"
                           :title="dailyChartTitle(item.etf_code, processedData.weekDays[col.dayIdx])"
                           @click.stop="openDailyChartViewer(item)"></i>
                      </div>
                    </template>
                    <template v-else>
                      <div class="dash-cell-inner flex flex-col sm:flex-row items-center justify-center gap-0.5 sm:gap-1" :title="weekDataTitle(item)">
                        <span class="text-[10px] sm:text-sm font-mono leading-tight">{{ item.week_status || '-' }}</span>
                        <i class="fa-regular fa-image text-slate-400 hover:text-blue-500 cursor-pointer text-sm sm:text-xs shrink-0 p-1 -m-0.5"
                           :title="weekChartTitle()"
                           @click.stop="openWeeklyChartViewer(item)"></i>
                      </div>
                    </template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <p class="text-[11px] text-slate-400 text-center">
          单元格格式：上午/下午|日线。未触发显示 “-”。列按最新采集弹匣轮转（最右为最新）。默认按最右列绝对值排序；免费 TOP3 → 收藏 → 其余。手机端仅显示最新 2 列。
        </p>

        <div v-if="tipEnabled" class="text-center pt-2">
          <button type="button" @click="tipChannel = tipWechatSrc ? 'wechat' : (tipAlipaySrc ? 'alipay' : 'wechat'); tipVisible = true"
                  class="text-xs text-slate-400 hover:theme-text underline">
            {{ settings.tip_note || '觉得有用？请作者喝杯咖啡' }}
          </button>
        </div>
      </template>

      <div v-if="tipVisible" class="fixed inset-0 modal-overlay z-[100] flex items-center justify-center p-4" @click.self="tipVisible = false">
        <div class="bg-white rounded-2xl w-full max-w-sm p-6 space-y-4 shadow-2xl text-center">
          <h3 class="font-bold text-slate-800">感谢支持</h3>
          <p class="text-xs text-slate-500">{{ settings.tip_note || '自愿打赏，不解锁任何权限' }}</p>
          <div class="flex justify-center gap-2 mb-1" v-if="tipWechatSrc || tipAlipaySrc">
            <button type="button" v-if="tipWechatSrc" @click="tipChannel='wechat'"
                    class="px-3 py-1 rounded-full text-xs border transition"
                    :class="tipChannel==='wechat' ? 'theme-bg text-white border-transparent' : 'bg-white text-slate-600 border-slate-200'">微信</button>
            <button type="button" v-if="tipAlipaySrc" @click="tipChannel='alipay'"
                    class="px-3 py-1 rounded-full text-xs border transition"
                    :class="tipChannel==='alipay' ? 'theme-bg text-white border-transparent' : 'bg-white text-slate-600 border-slate-200'">支付宝</button>
          </div>
          <div class="flex justify-center">
            <div v-if="tipChannel==='wechat' && tipWechatSrc" class="space-y-1">
              <img :src="tipWechatSrc" class="w-40 h-40 object-contain border rounded-lg mx-auto" alt="微信收款码">
              <div class="text-[11px] text-slate-500">微信扫码</div>
            </div>
            <div v-else-if="tipChannel==='alipay' && tipAlipaySrc" class="space-y-1">
              <img :src="tipAlipaySrc" class="w-40 h-40 object-contain border rounded-lg mx-auto" alt="支付宝收款码">
              <div class="text-[11px] text-slate-500">支付宝扫码</div>
            </div>
            <p v-else class="text-xs text-slate-400">后台尚未配置打赏收款码</p>
          </div>
          <button type="button" @click="tipVisible = false" class="text-sm text-slate-500">关闭</button>
        </div>
      </div>
    </div>
  `,
};
