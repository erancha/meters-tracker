// Every user-visible string of the app. Hebrew lives only in this module.

// The day strip's list buttons drop their word on the narrowest phones, keeping the count.
const SAVED_WORD = "בוצעו";
const HELD_WORD = "ממתינות";

export const texts = {
  appTitle: "מונים AI",
  appVersion: (version: string, date: string) => `גירסה ${version}, ${date}`,
  signIn: "התחברות עם Google",
  // The line under the name on the landing page: what the app is for.
  appTagline: "חיוב דירות לפי מוני מים וחשמל",
  // Landing copy. A **phrase** is set in bold. It condenses the root README's overview for a
  // landlord, and must stay aligned with it.
  landingPitch: [
    "**מצלמים את המונה, והמערכת עושה את השאר:** קוראת אותו, ומחשבת את הצריכה ואת החיוב של כל דירה.",
    "**הכול בצ'אט, בעברית ובלי טפסים:** המערכת מציעה, ומאשרים בכפתור או מתקנים במילים פשוטות.",
  ],
  // The pitch as numbered steps, since visitors skim prose but follow steps.
  landingSteps: [
    {
      title: "מצלמים את המונה בצ'אט",
      text: "מונה מים או חשמל. המערכת קוראת את מספר המונה ואת הקריאה, ושומרת רק אחרי אישור.",
    },
    {
      title: "רואים את הצריכה והחיוב",
      text: "אחרי כל קריאה: הקריאה הקודמת, הצריכה מאז ומתחילת התקופה, והחיוב לפי התעריפים.",
    },
    {
      title: "סוגרים את תקופת החיוב",
      text: "קריאה בסוף התקופה מציעה לסגור אותה, וסיכום התקופה של הדירה מגיע במייל.",
    },
  ],
  // What the "more" toggle adds under the steps.
  landingDetails: [
    "תמונה לא ברורה? המערכת **לא מנחשת**: היא מבקשת צילום חוזר או הקלדה.",
    "מונה חדש משויך לנכס ולדירה, שנוצרים לפי שם.",
    "כל דירה מחויבת לפי **תקופות**, חודשיים כברירת מחדל, והתקופה הבאה נפתחת למחרת הסגירה.",
    "אפשר גם לסגור תקופה מוקדם, לסמן דירה כפנויה, או לקבוע תאריך התחלה לדייר חדש, הכול במילים.",
    "**תעריפים** מוקלדים בצ'אט או נקראים מצילום של חשבון קודם.",
    "**קריאות חשודות נבדקות:** קריאה שנייה באותו יום, ערך נמוך מהקודם, או קפיצה חריגה בצריכה.",
    "במסך רחב, חלונית ליד הצ'אט מציגה את הקריאות של היום.",
  ],
  // The toggle under the landing's sign-in button, for a visitor wary of signing in to an
  // unfamiliar site with a Google account, and the text it opens. The first two points answer the
  // fear of handing the app access to a Google account, since a worried reader discounts advantages
  // until then; the last two argue the advantages.
  signInWhy: "למה להתחבר עם Google?",
  signInWhyLead: "כניסה עם Google **בטוחה ונוחה יותר** מפתיחת חשבון עם סיסמה חדשה.",
  signInWhyPoints: [
    "**ההתחברות מאושרת על ידי Google.\u200F** Google מוסרת לאפליקציה רק אימות לגבי הזהות שלכם, ללא גישה לנתונים שלכם (מיילים, אנשי קשר ועוד).",
    "**השליטה אצלכם:** אפשר לנתק את האפליקציה בכל רגע מהגדרות חשבון Google.",
    "**אין עוד סיסמה לזכור,** ואין סיסמה שיכולה לדלוף מכאן: האפליקציה לא שומרת סיסמאות בכלל.",
    "**ההגנות של Google עובדות בשבילכם:** אימות דו-שלבי והתראה על כניסה חשודה שומרים גם על הכניסה לכאן.",
  ],
  signInWhyLink: "ההסבר המלא של Google",
  signInWhyClose: "סגירת ההסבר",
  // What a link that opens a new tab adds to its name, where the eye sees ↗.
  newTab: "(נפתח בכרטיסייה חדשה)",
  more: "יותר",
  less: "פחות",
  demo: "הדגמה",
  demoHint: "ניתן לשנות את המהירות; מומלץ לראות במסך מחשב",
  // The marks before the demo and invite links, the same as the invite message's own.
  demoMark: "▶️",
  inviteMark: "💬",
  invite: "הזמנת חברים ב-WhatsApp",
  // The invite's paragraphs, in the voice of a friend passing on a recommendation, since a
  // landing-page visitor is not the developer. The page's own address follows the last one.
  inviteMessage: [
    "היי! 👋 קיבלתי המלצה על אפליקציה לחיוב דירות לפי מוני מים וחשמל, למי שמשכיר דירות.",
    "📷 מצלמים את המונה בצ'אט, והיא קוראת אותו, מחשבת את הצריכה ואת החיוב, ומנהלת תקופות חיוב" +
      " לכל דירה.",
    "אין טפסים: היא מציעה, ומאשרים בכפתור או מתקנים במילים פשוטות.",
    "▶️ יש שם גם הדגמה, שאפשר לראות בלי להתחבר:",
  ],
  source: "קוד המקור ב-GitHub",
  signInAnotherAccount: "התחברות עם חשבון Google אחר",
  copyAdminEmail: "העתקת כתובת מנהל המערכת",
  adminEmailCopied: (address: string) => `הכתובת ${address} הועתקה ללוח`,
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
  // The opening line of the backend's welcome to a new owner's account (texts.welcome).
  newAccountOpener: "שלום. החשבון חדש ועדיין ריק.",
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
