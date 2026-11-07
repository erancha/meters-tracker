// Every user-visible string of the app. Hebrew lives only in this module.

// The day strip's list buttons drop their word on the narrowest phones, keeping the count.
const SAVED_WORD = "בוצעו";
const HELD_WORD = "ממתינות";

export const texts = {
  appTitle: "מונים AI",
  appVersion: (version: string, date: string) => `גירסה ${version}, ${date}`,
  signIn: "התחברות עם Google",
  // Landing copy. A **phrase** is set in bold. The summary bullets condense the main shipped items
  // of the root README's overview and must stay aligned with it.
  landingCondensed: [
    "חיוב דירות לפי **מוני מים וחשמל**: **מצלמים את המונה**, והמערכת קוראת אותו, מחשבת את **הצריכה** ואת **החיוב**.",
    "הכול ב**צ'אט**, בעברית ובלי טפסים: המערכת מציעה, ומאשרים בכפתור או מתקנים במילים פשוטות.",
    "**תקופות חיוב** לכל דירה, **סגירת תקופה** עם הקריאה האחרונה, ו**תעריפים** שמוקלדים בצ'אט או נקראים מצילום של חשבון קודם.",
  ],
  landingIntro: "מעקב מוני מים וחשמל של דירות להשכרה, וחיוב כל דירה לפי תקופות — הכול בצ'אט אחד:",
  landingSummary: [
    "המערכת קוראת מהצילום את מספר המונה ואת הקריאה, ושומרת רק אחרי אישור. תמונה לא ברורה? המערכת תבקש צילום חוזר או הקלדה.",
    "אין טפסים: המערכת מציעה, ומאשרים בכפתור או מתקנים במילים.",
    "מונה חדש משויך לנכס ולדירה, שנוצרים לפי שם.",
    "כל דירה מחויבת לפי תקופות, חודשיים כברירת מחדל. קריאה בסוף התקופה מציעה לסגור אותה, והבאה נפתחת למחרת.",
    "אפשר גם לסגור תקופה מוקדם, או לקבוע תאריך התחלה לדייר חדש.",
    "אחרי כל קריאה מוצגים הצריכה והחיוב, לפי תעריפים שמוקלדים בצ'אט או נקראים מחשבון קודם.",
    "קריאות חשודות נבדקות: קריאה שנייה באותו יום, או ערך נמוך מהקודם.",
  ],
  more: "יותר",
  less: "פחות",
  demo: "הדגמה",
  demoHint: "(ניתן לשנות את המהירות; מומלץ לראות במסך מחשב)",
  invite: "הזמנת חברים ב-WhatsApp",
  // A landing-page visitor is not the developer, so the invite speaks as a received recommendation.
  // The address closes the message on a line of its own, so WhatsApp renders it as a tappable link.
  inviteMessage: (address: string) => [
    "📟 היי! קיבלתי המלצה על אפליקציה לחיוב דירות לפי מוני מים וחשמל",
    "📷 מצלמים את המונה בצ'אט, והיא קוראת אותו ומחשבת את הצריכה והחיוב",
    "",
    "נכנסים עם חשבון Google 👇",
    address,
  ].join("\n"),
  source: "קוד המקור ב-GitHub",
  signInIncomplete: "ההתחברות לא הושלמה — יש לרענן את הדף ולהתחבר מחדש",
  signOut: "התנתקות",
  accountMenu: "תפריט חשבון",
  dayTitle: "היום",
  previousDay: "היום הקודם",
  nextDay: "היום הבא",
  pickDay: "בחירת יום",
  backToToday: "היום",
  // Shown above the composer while an earlier day is shown: what is sent now is recorded today.
  earlierDayNotice: (dayMonth: string) => `מוצג ${dayMonth} · שינויים נרשמים היום`,
  resizePanel: "שינוי רוחב חלונית היום",
  weekdays: ["יום ראשון", "יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת"],
  savedCount: (n: number) => `קריאות שבוצעו · ${n}`,
  heldCount: (n: number) => `קריאות ממתינות · ${n}`,
  savedWord: SAVED_WORD,
  heldWord: HELD_WORD,
  savedShort: (n: number) => `${SAVED_WORD} ${n}`,
  heldShort: (n: number) => `${HELD_WORD} ${n}`,
  closeList: "סגירת הרשימה",
  held: { awaiting: "לאישור", failed: "לשליחה", reading: "בקריאה" },
  noReadingsToday: "לא נקראו מונים היום",
  noReadingsThatDay: "לא נקראו מונים ביום זה",
  tariffsMissing: "חסרים תעריפים",
  meterTypes: { water: "מים", electricity: "חשמל" } as Record<string, string>,
  crashed: "משהו השתבש. רעננו את הדף ונסו שוב.",
  backToStart: "חזרה למסך הראשי",
  thinking: "חושב",
  liveCard: "ממתין לתשובה",
  queuedCard: "בתור",
  turnFailed: "ההודעה לא נשלחה. אפשר לנסות שוב.",
  notHandled: "לא טופל",
  retry: "ניסיון חוזר",
  retried: (time: string) => `נשלח שוב ${time}`,
  loadFailed: "לא הצלחנו לטעון את השיחה. רעננו את הדף ונסו שוב.",
  composerPlaceholder: "הודעה, או צילום של מונה",
  send: "שליחה",
  attach: "צילום מונה",
  close: "סגירה",
  photo: "תמונת מונה",
  photoUnavailable: "לא ניתן להציג את התמונה",
  imageRejected: "התמונה לא התקבלה. אפשר לשלוח תמונת JPEG או PNG קטנה יותר.",
};
