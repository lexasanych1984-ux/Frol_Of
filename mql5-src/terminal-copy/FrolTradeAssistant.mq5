//+------------------------------------------------------------------+
//| FrolTradeAssistant.mq5                                           |
//| Панель ручной торговли: лот от риска, линии SL/TP, БУ, закрытия. |
//| Работает на демо и реальных счетах без ограничений.              |
//+------------------------------------------------------------------+
#property copyright "Frolov Aleksei"
#property version   "1.77"
#property description "Торговый ассистент: лот от риска %, вход по линиям или ценам, отложки стоп/лимит, БУ, частичное закрытие"

#include <Trade\Trade.mqh>

input double InpRiskPct    = 1.0;     // Риск на сделку, % от equity
input double InpRR         = 2.0;     // RR для авто-тейка (если TP не задан)
input long   InpMagic      = 777001;  // Magic ордеров панели
input int    InpDeviation  = 20;      // Проскальзывание, пункты
input double InpUIScale    = 1.0;     // Доп. масштаб панели (1.0 = авто по DPI)

CTrade trade;

// имена объектов
#define PFX "FTA_"
const string BG      = PFX"bg";
const string TBAR    = PFX"titlebar";
const string LB_TTL  = PFX"title";
const string LB_RISK = PFX"lb_risk";
const string ED_RISK = PFX"ed_risk";
const string LB_RR   = PFX"lb_rr";
const string ED_RR   = PFX"ed_rr";
const string LB_LOT  = PFX"lb_lot";
const string ED_LOT  = PFX"ed_lot";
const string LB_LOTH = PFX"lb_lothint";
const string BT_LINES= PFX"bt_lines";
const string LB_EN   = PFX"lb_entry";
const string ED_EN   = PFX"ed_entry";
const string LB_SL   = PFX"lb_sl";
const string ED_SL   = PFX"ed_sl";
const string LB_TP   = PFX"lb_tp";
const string ED_TP   = PFX"ed_tp";
const string LB_INFO = PFX"lb_info";
const string LB_INFO2= PFX"lb_info2";
const string BT_BUY  = PFX"bt_buy";
const string BT_SELL = PFX"bt_sell";
const string BT_PBUY = PFX"bt_pbuy";
const string BT_PSELL= PFX"bt_psell";
const string BT_BE   = PFX"bt_be";
const string LB_PART = PFX"lb_part";
const string BT_Q1   = PFX"bt_q1";
const string BT_Q2   = PFX"bt_q2";
const string BT_Q3   = PFX"bt_q3";
const string BT_CLOSE= PFX"bt_close";
const string LN_SL   = PFX"line_sl";
const string LN_TP   = PFX"line_tp";
const string LN_EN   = PFX"line_entry";

double g_risk, g_rr;
// Последнее сообщение панели: Comment() рисуется в левом верхнем углу графика,
// то есть прямо ПОД панелью и не виден. Поэтому дублируем текст в инфо-строку
// и придерживаем его там несколько секунд, чтобы UpdateInfo() не затёр сразу.
string g_msg      = "";
ulong  g_msgUntil = 0;
// Риск и лот — две стороны одного расчёта, поэтому у них есть ВЕДУЩИЙ параметр:
// то поле, которое пользователь правил последним. Второе считается от него и
// обновляется само при перетаскивании линий SL/Вход.
bool   g_lotDriven = false;   // false: ведёт «Риск %», true: ведёт «Лот»
double g_manualLot = 0;       // объём, заданный руками (когда g_lotDriven)
// Линия входа «примагничена» к текущей цене: пока флаг взведён, средняя линия
// (Вход) на каждом тике переставляется на bid — до первого перетаскивания линии
// или ручного ввода в поле «Вход». Тогда магнит отпускается и линия остаётся там,
// где её закрепили. g_mouseDown гасит магнит, пока зажата ЛКМ, чтобы линия не
// вырывалась из-под курсора во время перетаскивания.
bool   g_entryMagnet = false;
bool   g_mouseDown   = false;
#define MSG_HOLD_MS 6000
double g_k = 1.0;   // общий масштаб (DPI * InpUIScale)

// палитра: спокойный графит, приглушённые акценты
const color COL_BG      = C'21,24,31';
const color COL_BORDER  = C'52,58,70';
const color COL_TBAR    = C'27,31,40';
const color COL_TITLE   = C'198,205,215';
const color COL_TEXT    = C'222,226,232';
const color COL_MUTED   = C'128,136,148';
const color COL_EDIT_BG = C'31,36,45';
const color COL_EDIT_FG = C'230,234,240';
const color COL_NEUTRAL = C'42,48,60';
const color COL_BUY     = C'43,110,87';
const color COL_SELL    = C'166,66,66';
const color COL_PBUY    = C'34,73,60';
const color COL_PSELL   = C'110,52,52';
const color COL_BE      = C'80,76,52';
const color COL_HALF    = C'52,56,72';
const color COL_CLOSE   = C'116,52,52';

int S(double v)  { return (int)MathRound(v * g_k); }  // масштаб размеров
int FS(double v) { return (int)MathRound(v * g_k); }  // масштаб шрифтов

int g_panelW = 0;                       // ширина содержимого панели, px
#define FONT_I1 "Segoe UI Semibold"     // шрифт крупной инфо-строки
#define FONT_I2 "Segoe UI"              // шрифт мелкой инфо-строки

//+------------------------------------------------------------------+
//| Вывод в инфо-строки. Ширину меряем в ПИКСЕЛЯХ тем же шрифтом, что |
//| у метки: счёт символов врёт (кириллица, цифры и пробелы разной    |
//| ширины), и текст вылезал за панель на график.                     |
//+------------------------------------------------------------------+
int TextPx(const string s, const string font, int fs)
  {
   TextSetFont(font, -fs * 10, 0, 0);   // отрицательный размер = 1/10 пункта
   uint w = 0, h = 0;                   // TextGetSize принимает именно uint&
   TextGetSize(s, w, h);
   return (int)w;
  }

string FitPx(string s, const string font, int fs, int maxPx)
  {
   if(maxPx <= 0 || TextPx(s, font, fs) <= maxPx) return s;
   while(StringLen(s) > 1 && TextPx(s + "…", font, fs) > maxPx)
      s = StringSubstr(s, 0, StringLen(s) - 1);
   return s + "…";
  }

//| Две строки как есть, каждая ужимается под ширину панели           |
void PanelInfo(const string big, const string small)
  {
   ObjectSetString(0, LB_INFO,  OBJPROP_TEXT, FitPx(big, FONT_I1, FS(9), g_panelW));
   // пустая строка заставляет MT5 показать заглушку "Label" — ставим пробел
   string s = (small == "" ? " " : FitPx(small, FONT_I2, FS(7), g_panelW));
   ObjectSetString(0, LB_INFO2, OBJPROP_TEXT, s);
  }

//| Длинное сообщение: переносим по пробелу на вторую строку          |
void PanelMsg(const string msg)
  {
   if(TextPx(msg, FONT_I1, FS(9)) <= g_panelW) { PanelInfo(msg, ""); return; }
   int cut = -1;
   for(int i = StringLen(msg) - 1; i > 4; i--)
     {
      if(StringGetCharacter(msg, i) != ' ') continue;
      if(TextPx(StringSubstr(msg, 0, i), FONT_I1, FS(9)) <= g_panelW) { cut = i; break; }
     }
   if(cut < 0) { PanelInfo(msg, ""); return; }   // одно длинное слово — обрежется
   PanelInfo(StringSubstr(msg, 0, cut), StringSubstr(msg, cut + 1));
  }

//+------------------------------------------------------------------+
void CreateLabel(const string name, int x, int y, const string text,
                 color clr, int fs, const string font = "Segoe UI")
  {
   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, font);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fs);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

void CreateEdit(const string name, int x, int y, int w, int h, const string text, int fs)
  {
   ObjectCreate(0, name, OBJ_EDIT, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, "Segoe UI");
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, COL_EDIT_BG);
   ObjectSetInteger(0, name, OBJPROP_COLOR, COL_EDIT_FG);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, COL_BORDER);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fs);
   ObjectSetInteger(0, name, OBJPROP_ALIGN, ALIGN_CENTER);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

void CreateButton(const string name, int x, int y, int w, int h,
                  const string text, color bg, int fs,
                  color fg = clrWhite, const string font = "Segoe UI")
  {
   ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, font);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_COLOR, fg);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, COL_BORDER);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fs);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

void CreatePanel(const string name, int x, int y, int w, int h, color bg, color border)
  {
   ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_COLOR, border);
   ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

void CreateHLine(const string name, double price, color clr, const string text)
  {
   ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DASH);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTED, true);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   g_risk = InpRiskPct;
   g_rr   = InpRR;
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviation);

   // авто-масштаб под DPI монитора, слегка компактнее (x0.85)
   int dpi = (int)TerminalInfoInteger(TERMINAL_SCREEN_DPI);
   if(dpi <= 0) dpi = 96;
   g_k = dpi / 96.0 * 0.85 * (InpUIScale > 0 ? InpUIScale : 1.0);

   int W  = S(248);            // ширина содержимого
   g_panelW = W;               // по ней ужимаются инфо-строки
   int x  = S(16);
   int y0 = S(40);
   int half = (W - S(8)) / 2;  // ширина кнопки в паре
   int ew   = (W - S(12)) / 3; // ширина поля цены в тройке

   // раскладка по рядам (фон создаётся ПЕРВЫМ — в MT5 рисуется в порядке создания)
   int yTitle = y0;
   int y1     = y0 + S(30);
   int yRisk  = y1;
   int yLot   = yRisk  + S(32);       // ручной лот (пусто = считать от риска)
   int yLines = yLot   + S(32);
   int yPLbl  = yLines + S(32);       // подписи Вход/SL/TP
   int yPEd   = yPLbl  + S(16);       // поля цен
   int yInfo  = yPEd   + S(30);
   int yInfo2 = yInfo  + S(20);
   int yBuy   = yInfo2 + S(22);
   int yPend  = yBuy   + S(42);
   int yBe    = yPend  + S(32);
   int yPart  = yBe    + S(32);
   int yClose = yPart  + S(32);
   int yEnd   = yClose + S(36);
   int bgTop  = y1 - S(6);

   CreatePanel(BG, x - S(8), bgTop, W + S(16), yEnd - bgTop, COL_BG, COL_BORDER);
   CreatePanel(TBAR, x - S(8), yTitle - S(6), W + S(16), S(27), COL_TBAR, COL_BORDER);
   CreateLabel(LB_TTL, x, yTitle, "FROL ASSISTANT · " + _Symbol, COL_TITLE, FS(9), "Segoe UI Semibold");

   // риск / RR
   CreateLabel(LB_RISK, x, yRisk + S(5), "Риск %", COL_MUTED, FS(8));
   CreateEdit(ED_RISK, x + S(52), yRisk, S(60), S(24), DoubleToString(g_risk, 3), FS(9));
   CreateLabel(LB_RR, x + S(130), yRisk + S(5), "RR", COL_MUTED, FS(8));
   CreateEdit(ED_RR, x + S(156), yRisk, S(60), S(24), DoubleToString(g_rr, 2), FS(9));

   // ручной лот: заполнено — берём его как есть, пусто — считаем от риска
   CreateLabel(LB_LOT, x, yLot + S(5), "Лот", COL_MUTED, FS(8));
   CreateEdit(ED_LOT, x + S(52), yLot, S(60), S(24), "", FS(9));
   CreateLabel(LB_LOTH, x + S(120), yLot + S(7), "связан с риском", COL_MUTED, FS(7));

   // линии
   CreateButton(BT_LINES, x, yLines, W, S(26), "Линии SL / TP / Вход", COL_NEUTRAL, FS(8));

   // ручные цены: пусто = брать с линии; линии сами заполняют поля
   CreateLabel(LB_EN, x, yPLbl, "Вход", COL_MUTED, FS(7));
   CreateLabel(LB_SL, x + ew + S(6), yPLbl, "SL", COL_MUTED, FS(7));
   CreateLabel(LB_TP, x + 2 * (ew + S(6)), yPLbl, "TP", COL_MUTED, FS(7));
   CreateEdit(ED_EN, x, yPEd, ew, S(24), "", FS(8));
   CreateEdit(ED_SL, x + ew + S(6), yPEd, ew, S(24), "", FS(8));
   CreateEdit(ED_TP, x + 2 * (ew + S(6)), yPEd, ew, S(24), "", FS(8));

   // инфо
   CreateLabel(LB_INFO, x, yInfo, "Лот — · нет SL", COL_TEXT, FS(9), "Segoe UI Semibold");
   CreateLabel(LB_INFO2, x, yInfo2, "", COL_MUTED, FS(7));

   // вход по рынку
   CreateButton(BT_BUY, x, yBuy, half, S(34), "BUY", COL_BUY, FS(11), clrWhite, "Segoe UI Semibold");
   CreateButton(BT_SELL, x + half + S(8), yBuy, half, S(34), "SELL", COL_SELL, FS(11), clrWhite, "Segoe UI Semibold");

   // отложки (тип стоп/лимит определяется по цене входа — подпись обновляется сама)
   CreateButton(BT_PBUY, x, yPend, half, S(25), "Отлож. BUY", COL_PBUY, FS(8));
   CreateButton(BT_PSELL, x + half + S(8), yPend, half, S(25), "Отлож. SELL", COL_PSELL, FS(8));

   // безубыток
   CreateButton(BT_BE, x, yBe, W, S(25), "SL → безубыток", COL_BE, FS(8));

   // частичное закрытие: выбор доли
   CreateLabel(LB_PART, x, yPart + S(5), "Закрыть", COL_MUTED, FS(8));
   int bw = (W - S(62) - S(12)) / 3;
   CreateButton(BT_Q1, x + S(62), yPart, bw, S(25), "1/4", COL_HALF, FS(8));
   CreateButton(BT_Q2, x + S(62) + bw + S(6), yPart, bw, S(25), "1/2", COL_HALF, FS(8));
   CreateButton(BT_Q3, x + S(62) + 2 * (bw + S(6)), yPart, bw, S(25), "3/4", COL_HALF, FS(8));

   CreateButton(BT_CLOSE, x, yClose, W, S(27), "Закрыть всё + отложки", COL_CLOSE, FS(9));

   ChartSetInteger(0, CHART_EVENT_MOUSE_MOVE, true);  // ловим зажатую ЛКМ для магнита
   EventSetMillisecondTimer(500);
   ChartRedraw();
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   ChartSetInteger(0, CHART_EVENT_MOUSE_MOVE, false);
   Comment("");
   ObjectsDeleteAll(0, PFX);
   ChartRedraw();
  }

//+------------------------------------------------------------------+
//| Чтение поля цены: пусто/мусор = 0 (не задано)                    |
//+------------------------------------------------------------------+
//| Разбор числа из поля: запятая и точка равноправны как разделитель |
double ParseNum(string t)
  {
   StringReplace(t, ",", ".");
   StringTrimLeft(t);
   StringTrimRight(t);
   if(t == "") return 0;
   return StringToDouble(t);
  }

double EditVal(const string name)
  {
   double v = ParseNum(ObjectGetString(0, name, OBJPROP_TEXT));
   return (v > 0 ? v : 0);
  }

double LinePrice(const string name)
  {
   if(ObjectFind(0, name) < 0) return 0;
   return ObjectGetDouble(0, name, OBJPROP_PRICE);
  }

// эффективные цены: ручное поле приоритетнее линии
double EffSL()    { double v = EditVal(ED_SL); return v > 0 ? v : LinePrice(LN_SL); }
double EffEntry() { double v = EditVal(ED_EN); return v > 0 ? v : LinePrice(LN_EN); }
double EffTPraw() { double v = EditVal(ED_TP); return v > 0 ? v : LinePrice(LN_TP); }

//| Цена, от которой считаем сделку: поле/линия входа, иначе рынок    |
double CurrentEntry(double sl)
  {
   double v = EffEntry();
   if(v > 0) return v;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   return (sl < bid) ? ask : bid;
  }

//+------------------------------------------------------------------+
//| Расчёт лота от риска: дистанция до SL -> убыток на 1 лот         |
//+------------------------------------------------------------------+
//| Убыток на 1 лот при движении от entry до sl (0 = данных нет)      |
double LossPerLot(double entry, double sl)
  {
   double dist = MathAbs(entry - sl);
   if(dist <= 0) return 0;
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0 || tickVal <= 0) return 0;
   return dist / tickSize * tickVal;
  }

//| Ручной лот из поля (0 = поле пустое, считаем от риска)            |
double EffLot() { return EditVal(ED_LOT); }

//| Записать в поле только при реальном изменении — иначе поле мигает |
//| и сбрасывает курсор, пока пользователь в нём набирает.            |
void SyncEdit(const string name, const string value)
  {
   if(ObjectGetString(0, name, OBJPROP_TEXT) != value)
      ObjectSetString(0, name, OBJPROP_TEXT, value);
  }

//| Привести лот к шагу и проверить границы. err != "" — лот негоден. |
double NormalizeLot(double lot, string &err)
  {
   err = "";
   double minL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step > 0) lot = MathRound(lot / step) * step;
   lot = NormalizeDouble(lot, 8);
   if(lot < minL)
     {
      err = StringFormat("Лот %s меньше минимального %s",
                         DoubleToString(lot, 2), DoubleToString(minL, 2));
      return 0;
     }
   if(lot > maxL)
     {
      err = StringFormat("Лот %s больше максимального %s",
                         DoubleToString(lot, 2), DoubleToString(maxL, 2));
      return 0;
     }
   return lot;
  }

double CalcLot(double entry, double sl, double &riskMoney, double &lossPerLot)
  {
   riskMoney  = AccountInfoDouble(ACCOUNT_EQUITY) * g_risk / 100.0;
   lossPerLot = LossPerLot(entry, sl);
   if(lossPerLot <= 0) return 0;
   double lot = riskMoney / lossPerLot;

   double minL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step > 0) lot = MathFloor(lot / step) * step;
   if(lot < minL) return 0;          // риск слишком мал для мин. лота
   if(lot > maxL) lot = maxL;
   return NormalizeDouble(lot, 8);
  }

//+------------------------------------------------------------------+
//| Итоговый объём сделки: ручной лот приоритетнее риска.             |
//| err != "" — открывать нельзя, текст уже готов для Notify.         |
//+------------------------------------------------------------------+
double ResolveLot(double entry, double sl, double &riskMoney, double &lossPerLot,
                  bool &manual, string &err, string &errShort)
  {
   err = ""; errShort = ""; manual = false;
   lossPerLot = LossPerLot(entry, sl);

   // Ведёт лот: объём фиксирован, риск пересчитывается от текущих линий.
   if(g_lotDriven && g_manualLot > 0)
     {
      manual = true;
      riskMoney = g_manualLot * lossPerLot;
      return g_manualLot;
     }

   double lot = CalcLot(entry, sl, riskMoney, lossPerLot);
   if(lot <= 0) err = LotZeroReason(entry, sl, lossPerLot, errShort);
   return lot;
  }

//+------------------------------------------------------------------+
//| Почему CalcLot вернул 0 — с готовым ответом, что вписать в Риск % |
//| Панель считает лот от риска, поля объёма у неё нет: попытка       |
//| задать "0.01 лота" превращается в риск 0.01% и обнуляет лот.      |
//+------------------------------------------------------------------+
string LotZeroReason(double entry, double sl, double lossPerLot, string &shortMsg)
  {
   if(lossPerLot <= 0)
     {
      shortMsg = "Лот = 0: некорректный SL";
      return "Лот = 0: проверь SL — дистанция до входа нулевая "
             "или цена осталась от другого инструмента";
     }

   double minL   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double needMoney = minL * lossPerLot;
   if(equity <= 0)
     {
      shortMsg = "Лот = 0: нет equity";
      return "Лот = 0: нет данных по equity счёта";
     }

   double needPct = needMoney / equity * 100.0;
   shortMsg = StringFormat("Лот 0 · нужен риск %.3f%%", needPct);
   return StringFormat("Лот = 0: нужен риск от %.3f%% (%.2f %s), сейчас %.3f%%. "
                       "SL %s, мин. лот %s",
                       needPct, needMoney, AccountInfoString(ACCOUNT_CURRENCY),
                       g_risk, DoubleToString(MathAbs(entry - sl), _Digits),
                       DoubleToString(minL, 2));
  }

//+------------------------------------------------------------------+
//| Тейк: ручное поле > линия TP > авто от RR                        |
//+------------------------------------------------------------------+
double GetTP(double entry, double sl, bool isBuy)
  {
   double tp = EffTPraw();
   if(tp > 0) return NormalizeDouble(tp, _Digits);
   double dist = MathAbs(entry - sl) * g_rr;
   return NormalizeDouble(isBuy ? entry + dist : entry - dist, _Digits);
  }

//+------------------------------------------------------------------+
//| Сообщение пользователю. В журнал уходит полный текст, на панель — |
//| короткий (места мало): длинный вылез бы за её край на график.     |
//+------------------------------------------------------------------+
void Notify(const string msg, const string shortMsg = "")
  {
   Print("[FrolAssistant] ", msg);
   Comment("FrolAssistant: ", msg);
   g_msg      = (shortMsg == "" ? msg : shortMsg);
   g_msgUntil = GetTickCount64() + MSG_HOLD_MS;

   PanelMsg(g_msg);
   ChartRedraw();
  }

//+------------------------------------------------------------------+
//| Вход по рынку                                                    |
//+------------------------------------------------------------------+
void MarketOrder(bool isBuy)
  {
   double sl = EffSL();
   if(sl <= 0) { Notify("Задай SL: линией или в поле SL"); return; }
   double entry = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                        : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if((isBuy && sl >= entry) || (!isBuy && sl <= entry))
     { Notify("SL не с той стороны от цены"); return; }
   double riskM = 0, perLot = 0;
   bool   manual = false;
   string err = "", errShort = "";
   double lot = ResolveLot(entry, sl, riskM, perLot, manual, err, errShort);
   if(err != "") { Notify(err, errShort); return; }
   double tp = GetTP(entry, sl, isBuy);
   sl = NormalizeDouble(sl, _Digits);
   bool ok = isBuy ? trade.Buy(lot, _Symbol, 0, sl, tp, "FrolAssistant")
                   : trade.Sell(lot, _Symbol, 0, sl, tp, "FrolAssistant");
   Notify((ok ? "Открыто: " : "ОШИБКА: ") + (isBuy ? "BUY " : "SELL ") +
          DoubleToString(lot, 2) + " лот" + (manual ? " (задан)" : "") +
          ", риск " + DoubleToString(riskM, 0) + " " +
          AccountInfoString(ACCOUNT_CURRENCY) + " | " + trade.ResultComment(),
          (ok ? "Открыто " : "ОШИБКА ") + (isBuy ? "BUY " : "SELL ") +
          DoubleToString(lot, 2) + " лот");
  }

//+------------------------------------------------------------------+
//| Отложенный ордер: цена входа из поля или с линии.                |
//| Тип стоп/лимит выбирается по положению входа к рынку.            |
//+------------------------------------------------------------------+
void PendingOrder(bool isBuy)
  {
   double entry = EffEntry();
   double sl    = EffSL();
   if(entry <= 0 || sl <= 0) { Notify("Задай Вход и SL: линиями или в полях цен"); return; }
   if((isBuy && sl >= entry) || (!isBuy && sl <= entry))
     { Notify("SL не с той стороны от входа"); return; }
   double riskM = 0, perLot = 0;
   bool   manual = false;
   string err = "", errShort = "";
   double lot = ResolveLot(entry, sl, riskM, perLot, manual, err, errShort);
   if(err != "") { Notify(err, errShort); return; }
   double tp = GetTP(entry, sl, isBuy);
   entry = NormalizeDouble(entry, _Digits);
   sl    = NormalizeDouble(sl, _Digits);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   bool ok;
   string kind;
   if(isBuy)
     {
      if(entry > ask) { kind = "BUY STOP";  ok = trade.BuyStop(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, "FrolAssistant"); }
      else            { kind = "BUY LIMIT"; ok = trade.BuyLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, "FrolAssistant"); }
     }
   else
     {
      if(entry < bid) { kind = "SELL STOP";  ok = trade.SellStop(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, "FrolAssistant"); }
      else            { kind = "SELL LIMIT"; ok = trade.SellLimit(lot, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, "FrolAssistant"); }
     }
   Notify((ok ? "Выставлен " : "ОШИБКА ") + kind + ": " + DoubleToString(lot, 2) +
          " лот @ " + DoubleToString(entry, _Digits) + " | " + trade.ResultComment());
  }

//+------------------------------------------------------------------+
//| Операции с открытыми позициями по текущему символу               |
//+------------------------------------------------------------------+
void Breakeven()
  {
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double tp   = PositionGetDouble(POSITION_TP);
      if(trade.PositionModify(tk, NormalizeDouble(open, _Digits), tp)) n++;
     }
   Notify("СТОП в БУ: изменено позиций — " + IntegerToString(n));
  }

void CloseFraction(double frac)
  {
   int n = 0;
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      double full = PositionGetDouble(POSITION_VOLUME);
      double vol  = full * frac;
      if(step > 0) vol = MathFloor(vol / step) * step;
      if(vol < minL) { Notify("Доля меньше мин. лота — пропуск"); continue; }
      if(vol >= full) vol = full;   // 3/4 от 1 лота с шагом 1 = закрыть весь
      if(vol >= full ? trade.PositionClose(tk) : trade.PositionClosePartial(tk, vol)) n++;
     }
   Notify("Закрыто " + DoubleToString(frac * 100, 0) + "%: позиций — " + IntegerToString(n));
  }

void CloseAll()
  {
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(trade.PositionClose(tk)) n++;
     }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong tk = OrderGetTicket(i);
      if(tk == 0 || OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      trade.OrderDelete(tk);
     }
   Notify("Закрыто позиций: " + IntegerToString(n) + " (+ снята отложка)");
  }

//+------------------------------------------------------------------+
//| Линии: поля цен заполняются с линий и при перетаскивании         |
//+------------------------------------------------------------------+
void SyncFieldsFromLines()
  {
   double v;
   v = LinePrice(LN_EN);
   ObjectSetString(0, ED_EN, OBJPROP_TEXT, v > 0 ? DoubleToString(v, _Digits) : "");
   v = LinePrice(LN_SL);
   ObjectSetString(0, ED_SL, OBJPROP_TEXT, v > 0 ? DoubleToString(v, _Digits) : "");
   v = LinePrice(LN_TP);
   ObjectSetString(0, ED_TP, OBJPROP_TEXT, v > 0 ? DoubleToString(v, _Digits) : "");
  }

//| Магнит входа: пока флаг взведён и не зажата ЛКМ, средняя линия      |
//| (Вход) едет за текущей ценой. Поле «Вход» держим пустым — EffEntry  |
//| берёт цену прямо с линии, а поле не мигает под набором.             |
void MagnetEntry()
  {
   if(!g_entryMagnet || g_mouseDown) return;
   if(ObjectFind(0, LN_EN) < 0) { g_entryMagnet = false; return; }
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(bid <= 0) return;
   double p = NormalizeDouble(bid, _Digits);
   if(ObjectGetDouble(0, LN_EN, OBJPROP_PRICE) != p)
      ObjectSetDouble(0, LN_EN, OBJPROP_PRICE, p);
  }

//| Отпустить магнит: линия входа больше не едет за ценой               |
void DetachMagnet()
  {
   if(!g_entryMagnet) return;
   g_entryMagnet = false;
   if(ObjectFind(0, LN_EN) >= 0)
      ObjectSetString(0, LN_EN, OBJPROP_TEXT, "Вход (для отложки)");
  }

void ToggleLines()
  {
   if(ObjectFind(0, LN_SL) >= 0)
     {
      ObjectDelete(0, LN_SL);
      ObjectDelete(0, LN_TP);
      ObjectDelete(0, LN_EN);
      g_entryMagnet = false;
      // очистить поля, чтобы не остались устаревшие цены
      ObjectSetString(0, ED_EN, OBJPROP_TEXT, "");
      ObjectSetString(0, ED_SL, OBJPROP_TEXT, "");
      ObjectSetString(0, ED_TP, OBJPROP_TEXT, "");
      ObjectSetString(0, BT_LINES, OBJPROP_TEXT, "Линии SL / TP / Вход");
     }
   else
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double off = bid * 0.003;
      CreateHLine(LN_SL, bid - off, clrOrangeRed, "SL");
      CreateHLine(LN_TP, bid + off * g_rr, clrLimeGreen, "TP");
      CreateHLine(LN_EN, bid, clrDeepSkyBlue, "Вход · магнит");
      SyncFieldsFromLines();
      // средняя линия примагничена к цене; поле «Вход» пустое = берётся с линии
      g_entryMagnet = true;
      ObjectSetString(0, ED_EN, OBJPROP_TEXT, "");
      ObjectSetString(0, BT_LINES, OBJPROP_TEXT, "Убрать линии");
     }
   ChartRedraw();
  }

//+------------------------------------------------------------------+
//| Обновление информации на панели                                  |
//+------------------------------------------------------------------+
void UpdateInfo()
  {
   // свежее сообщение держим на панели, не затирая его текущими цифрами
   if(GetTickCount64() < g_msgUntil) return;

   double sl  = EffSL();
   string cur = AccountInfoString(ACCOUNT_CURRENCY);
   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

   // подписи на кнопках отложек: какой тип получится при текущем входе
   double entryP = EffEntry();
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(entryP > 0)
     {
      ObjectSetString(0, BT_PBUY, OBJPROP_TEXT, entryP > ask ? "BUY Stop" : "BUY Limit");
      ObjectSetString(0, BT_PSELL, OBJPROP_TEXT, entryP < bid ? "SELL Stop" : "SELL Limit");
     }
   else
     {
      ObjectSetString(0, BT_PBUY, OBJPROP_TEXT, "Отлож. BUY");
      ObjectSetString(0, BT_PSELL, OBJPROP_TEXT, "Отлож. SELL");
     }

   if(sl <= 0)
     {
      if(!g_lotDriven) SyncEdit(ED_LOT, "");   // считать не от чего
      PanelInfo("Лот — · нет SL", "Спред " + IntegerToString(spread) + " пт");
      return;
     }
   double entry = (entryP > 0) ? entryP : ((sl < bid) ? ask : bid);

   // Фактический RR по текущим линиям — считаем ДО лота, чтобы он был виден
   // всегда, даже когда объём не набирается: при перетаскивании SL это
   // главное число на панели.
   bool   isBuy = (sl < entry);
   double tp    = GetTP(entry, sl, isBuy);
   double riskD = MathAbs(entry - sl);
   double rr    = (riskD > 0) ? MathAbs(tp - entry) / riskD : 0;
   string rrTxt = "RR " + DoubleToString(rr, 2);

   // Поле RR — это ТЕКУЩИЙ RR по линиям, а не застывшая цель: тянешь SL или
   // TP — число едет следом. Обратная сторона (ввод RR двигает тейк) сделана
   // в обработчике ED_RR.
   if(rr > 0)
     {
      SyncEdit(ED_RR, DoubleToString(rr, 2));
      if(rr <= 20) g_rr = rr;   // чтобы авто-тейк без линий стартовал отсюда
     }

   double riskM = 0, perLot = 0;
   bool   manual = false;
   string uErr = "", uErrShort = "";
   double lot = ResolveLot(entry, sl, riskM, perLot, manual, uErr, uErrShort);
   if(uErr != "")
     {
      if(!g_lotDriven) SyncEdit(ED_LOT, "");   // лот не набирается — поле пустое
      PanelInfo(uErrShort == "" ? uErr : uErrShort,
                rrTxt + " · спред " + IntegerToString(spread));
      return;
     }
   // Ведомое поле подтягиваем под ведущее: перетащил линии — второе число
   // пересчиталось само. Пишем только при изменении, иначе поле мигает.
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskPct = (equity > 0) ? riskM / equity * 100.0 : 0;
   if(manual)
     {
      g_risk = riskPct;              // держим в синхроне на случай возврата к риску
      SyncEdit(ED_RISK, DoubleToString(riskPct, 3));
     }
   else
      SyncEdit(ED_LOT, DoubleToString(lot, 2));

   PanelInfo("Лот " + DoubleToString(lot, 2) + (manual ? " (руч)" : "") +
             " · " + (isBuy ? "BUY" : "SELL") + " · " + rrTxt,
             "Риск " + DoubleToString(riskM, 0) + " " + cur +
             StringFormat(" · %.2f%%", riskPct) +
             " · спред " + IntegerToString(spread));
  }

void OnTimer() { MagnetEntry(); UpdateInfo(); ChartRedraw(); }
void OnTick()  { MagnetEntry(); UpdateInfo(); }

//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
  {
   // состояние ЛКМ: пока зажата — не дёргаем магнитом линию под курсором
   if(id == CHARTEVENT_MOUSE_MOVE)
     {
      g_mouseDown = ((int)StringToInteger(sparam) & 1) != 0;
      return;
     }
   // перетащили линию — цена сразу попадает в соответствующее поле
   if(id == CHARTEVENT_OBJECT_DRAG)
     {
      if(sparam == LN_EN)
        {
         DetachMagnet();   // потянули вход руками — магнит отпускаем
         ObjectSetString(0, ED_EN, OBJPROP_TEXT, DoubleToString(LinePrice(LN_EN), _Digits));
        }
      else if(sparam == LN_SL)
         ObjectSetString(0, ED_SL, OBJPROP_TEXT, DoubleToString(LinePrice(LN_SL), _Digits));
      else if(sparam == LN_TP)
         ObjectSetString(0, ED_TP, OBJPROP_TEXT, DoubleToString(LinePrice(LN_TP), _Digits));
      else return;
      UpdateInfo();
      ChartRedraw();
      return;
     }
   if(id == CHARTEVENT_OBJECT_ENDEDIT)
     {
      if(sparam == ED_RISK)
        {
         double v = ParseNum(ObjectGetString(0, ED_RISK, OBJPROP_TEXT));
         if(v > 0 && v <= 10)
           {
            g_risk = v;
            g_lotDriven = false;      // риск снова ведущий, лот считается от него
            g_manualLot = 0;
           }
         else Notify(StringFormat("Риск %% должен быть в пределах 0..10, оставил %.3f%%", g_risk));
         ObjectSetString(0, ED_RISK, OBJPROP_TEXT, DoubleToString(g_risk, 3));
        }
      if(sparam == ED_RR)
        {
         double v = ParseNum(ObjectGetString(0, ED_RR, OBJPROP_TEXT));
         if(v > 0 && v <= 20)
           {
            g_rr = v;
            // RR задан руками — двигаем под него ТЕЙК (и линию, и поле),
            // чтобы поле RR и картинка на графике всегда совпадали.
            double sl = EffSL();
            double en = CurrentEntry(sl);
            if(sl > 0 && en > 0)
              {
               bool   isBuy = (sl < en);
               double dist  = MathAbs(en - sl);
               double tp    = NormalizeDouble(isBuy ? en + dist * v : en - dist * v, _Digits);
               ObjectSetString(0, ED_TP, OBJPROP_TEXT, DoubleToString(tp, _Digits));
               if(ObjectFind(0, LN_TP) >= 0)
                  ObjectSetDouble(0, LN_TP, OBJPROP_PRICE, tp);
              }
           }
         else Notify(StringFormat("RR должен быть в пределах 0..20, оставил %.2f", g_rr));
         ObjectSetString(0, ED_RR, OBJPROP_TEXT, DoubleToString(g_rr, 2));
        }
      if(sparam == ED_LOT)
        {
         string t = ObjectGetString(0, ED_LOT, OBJPROP_TEXT);
         StringTrimLeft(t); StringTrimRight(t);
         if(t == "")                       // очистили — ведущим снова становится риск
           {
            g_lotDriven = false;
            g_manualLot = 0;
           }
         else
           {
            string err = "";
            double lot = NormalizeLot(ParseNum(t), err);
            if(err != "")
              {
               Notify(err + " — вернулся к расчёту от риска", err);
               g_lotDriven = false;
               g_manualLot = 0;
               ObjectSetString(0, ED_LOT, OBJPROP_TEXT, "");
              }
            else
              {
               g_manualLot = lot;
               g_lotDriven = true;   // теперь ведёт лот, риск считается от него
               ObjectSetString(0, ED_LOT, OBJPROP_TEXT, DoubleToString(lot, 2));
              }
           }
        }
      if(sparam == ED_EN || sparam == ED_SL || sparam == ED_TP)
        {
         // нормализуем: мусор -> пусто, число -> вид с точностью символа
         double v = EditVal(sparam);
         ObjectSetString(0, sparam, OBJPROP_TEXT, v > 0 ? DoubleToString(v, _Digits) : "");
         // вход задан руками = закрепить: магнит отпускаем, линию ставим на цену
         if(sparam == ED_EN && v > 0)
           {
            DetachMagnet();
            if(ObjectFind(0, LN_EN) >= 0)
               ObjectSetDouble(0, LN_EN, OBJPROP_PRICE, NormalizeDouble(v, _Digits));
           }
        }
      UpdateInfo();
      ChartRedraw();
      return;
     }
   if(id != CHARTEVENT_OBJECT_CLICK) return;
   // отжать кнопку обратно
   ObjectSetInteger(0, sparam, OBJPROP_STATE, false);

   if(sparam == BT_LINES) ToggleLines();
   else if(sparam == BT_BUY)   MarketOrder(true);
   else if(sparam == BT_SELL)  MarketOrder(false);
   else if(sparam == BT_PBUY)  PendingOrder(true);
   else if(sparam == BT_PSELL) PendingOrder(false);
   else if(sparam == BT_BE)    Breakeven();
   else if(sparam == BT_Q1)    CloseFraction(0.25);
   else if(sparam == BT_Q2)    CloseFraction(0.50);
   else if(sparam == BT_Q3)    CloseFraction(0.75);
   else if(sparam == BT_CLOSE) CloseAll();
   ChartRedraw();
  }
//+------------------------------------------------------------------+
