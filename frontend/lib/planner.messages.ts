// AA-1160 (scope 2, S285 live check) — the planner's own words in English and Vietnamese. An agency's planner
// (/{slug}/plan) opens in the agency's locale (CIS site settings, catalog view locale "en" | "vi"); every other page
// keeps English (pages open in English unless the agency or the user picks Vietnamese). The kit's typed catalogue
// (ui/core/i18n.ts defineMessages): tsc refuses a missing key or an argument mismatch, and lib/plannerI18n.test.ts
// checks the two sides at run time. The language reaches the components through components/PlannerLang.tsx.
// Agency names come in as arguments; no message names Adventure Asia (lib/agencyBrand.test.ts).
import { defineMessages, translator, type Catalog, type Lang, type Messages, type Translator } from "../ui/core/i18n.ts";
import { ADDONS } from "./dayAddons.messages.ts"; // AA-1152: the day cards' AA add-ons

const n_ = (n: number, one: string, many: string) => (n === 1 ? one : many);

// The planner's filter values (lib/types.ts ACTIVITIES, INTENSITIES) in Vietnamese.
const VALUE_VI: Record<string, string> = {
  trekking: "đi bộ đường dài",
  cultural_heritage: "di sản văn hoá",
  wildlife_nature: "thiên nhiên hoang dã",
  water_activities: "hoạt động dưới nước",
  culinary: "ẩm thực",
  wellness_relaxation: "nghỉ dưỡng thư giãn",
  adventure_sport: "thể thao mạo hiểm",
  local_immersion: "đời sống địa phương",
  leisurely: "nhẹ nhàng",
  moderate: "vừa phải",
  active: "năng động",
  strenuous: "vất vả",
};

export const PLANNER = defineMessages(
  {
    // app/plan/PlannerApp.tsx
    map: "Map",
    tourList: "List of tours",
    tripPanel: "Trip panel",
    yourTrip: "Your trip",
    yourTripShort: "Your trip",
    planWithChat: "Plan with chat",
    loadingChat: "Loading the chat",
    explore: "Explore",
    chat: "Chat",
    // components/Header.tsx (the agency bar)
    tripPlanner: "Trip Planner",
    legalAndDisplay: "Legal and display",
    // components/AiLabel.tsx AI_AGENCY_EXPLANATION (the English source stays there for the agency's other pages)
    aiAgencyExplanation:
      "Tour descriptions on this site are written with the help of AI and adapted to this agency's style; " +
      "the agency may have edited them. The planner's place search, its suggestions and the trip narration also use " +
      "AI. The agency confirms every detail of your trip in your official quote.",
  },
  {
    map: "Bản đồ",
    tourList: "Danh sách tour",
    tripPanel: "Bảng chuyến đi",
    yourTrip: "Chuyến đi của bạn",
    yourTripShort: "Chuyến đi",
    planWithChat: "Chat lên kế hoạch",
    loadingChat: "Đang tải khung trò chuyện",
    explore: "Khám phá",
    chat: "Trò chuyện",
    tripPlanner: "Lập kế hoạch chuyến đi",
    legalAndDisplay: "Pháp lý và hiển thị",
    aiAgencyExplanation:
      "Mô tả tour trên trang này được viết với sự hỗ trợ của AI và điều chỉnh theo phong cách của đại lý; đại lý có " +
      "thể đã chỉnh sửa. Tính năng tìm địa điểm, gợi ý và mô tả chuyến đi của trang lập kế hoạch cũng dùng AI. Đại lý " +
      "xác nhận mọi chi tiết chuyến đi trong báo giá chính thức.",
  },
);

export const TRIP = defineMessages(
  {
    yourTrip: "Your trip",
    kmByRoad: (km: number, flight: boolean) => `~${km} km by road${flight ? " +" : ""}`,
    unavailable: "Stops no longer available",
    remove: "Remove",
    removeNamed: (name: string) => `Remove ${name}`,
    loadingTrip: "Loading your trip",
    loadErrorTitle: "We couldn't load your trip",
    loadErrorBody: "Your trip is saved. Check your connection and try again.",
    tryAgain: "Try again",
    emptyTitle: "Start building your trip",
    emptyBody: "Pin experiences from the map and watch your day-by-day itinerary take shape here.",
    step1Title: "Choose a country",
    step1Body: "Use the filters top-left to focus the map on a destination.",
    step2Title: "Pin what interests you",
    step2Body: "Click a map marker, then add an experience to your trip.",
    step3Title: "Reorder & send",
    step3Body: "Drag the handles to reorder days and stops, then send the draft to an advisor.",
    tipLead: "Tip: try the search box to find experiences by activity, like",
    tipExample1: "“tea plantation trek”",
    tipOr: "or",
    tipExample2: "“temple visit”",
    days: (n: number) => n_(n, "Day", "Days"),
    countries: (n: number) => n_(n, "Country", "Countries"),
    kmByRoadShort: "km by road",
    reorderFailed: "We couldn't save the new order, so your days are back as they were. Please try again.",
    orderSaved: "New order saved.",
    undo: "Undo",
    tourDay: (from: number, to: number) => `tour day ${from}${to !== from ? `–${to}` : ""}`,
    transfer: "transfer",
    gap: (from: string, to: string, reason: string) =>
      `${from} → ${to}: ${reason}. Your advisor can suggest a bridge, or pick places on the same route.`,
    tooLong: (days: string) => `This trip is ${days} — quite long. You can keep adding, but consider trimming for a smoother journey.`,
    gettingThere: "Getting there",
    flyInto: "Fly into",
    thenTransfer: (hint: string) => `, then ${hint}`,
    aboutKm: (km: number) => ` (about ${km} km away)`,
    toFirstStop: " to your first stop.",
    headingHome: "Heading home",
    afterLastStop: (hint: string) => `After your last stop, ${hint}`,
    backTo: " back to ",
    flightHome: " for your flight home.",
    hintAirport: "you're right by the airport",
    hintTransfer: "a private 4WD or boat transfer",
    hintDrive: "a scenic overland drive or a domestic hop",
    hintFlight: "a domestic flight, then an overland transfer",
    yourAddition: "(your addition, not part of a tour)",
    removeLower: "remove",
    addFreeDay: "+ free day",
    addEndDay: "+ extra day at the end",
    kindExtraNight: "Extra night",
    kindFreeDay: "Free day",
    kindEndDay: "Extra day at the end",
    reorderHelp:
      "Drag a handle, or use the arrow keys on it, to reorder days and stops. Distances are rough estimates and travel " +
      "modes are suggestions — your advisor arranges the real transfers, boats and flights for an adventure route.",
    alsoLike: "You might also like",
    basedOnPins: "Based on what you've pinned.",
    narrationTitle: "Day-by-day narration",
    composing: "Composing…",
    regenerate: "Regenerate",
    compose: "✨ Compose",
    dayN: (n: number) => `Day ${n}`,
    sentToAgency: "✓ Sent to advisor",
    sendTo: (agency: string) => `Send to ${agency}`,
    sendToAdvisor: "Send to an advisor",
    confirmsLogistics: "An advisor confirms logistics before anything is booked.",
    poweredBy: "Powered by",
    yourAgency: "your travel agency",
  },
  {
    yourTrip: "Chuyến đi của bạn",
    kmByRoad: (km: number, flight: boolean) => `~${km} km đường bộ${flight ? " +" : ""}`,
    unavailable: "Các điểm dừng không còn phục vụ",
    remove: "Xoá",
    removeNamed: (name: string) => `Xoá ${name}`,
    loadingTrip: "Đang tải chuyến đi của bạn",
    loadErrorTitle: "Chúng tôi chưa tải được chuyến đi của bạn",
    loadErrorBody: "Chuyến đi của bạn đã được lưu. Hãy kiểm tra kết nối và thử lại.",
    tryAgain: "Thử lại",
    emptyTitle: "Bắt đầu xây dựng chuyến đi",
    emptyBody: "Ghim các trải nghiệm trên bản đồ và xem lịch trình từng ngày hình thành tại đây.",
    step1Title: "Chọn quốc gia",
    step1Body: "Dùng bộ lọc ở góc trên bên trái để đưa bản đồ về một điểm đến.",
    step2Title: "Ghim điều bạn quan tâm",
    step2Body: "Bấm vào một điểm trên bản đồ, rồi thêm một trải nghiệm vào chuyến đi.",
    step3Title: "Sắp xếp và gửi",
    step3Body: "Kéo tay nắm để sắp xếp lại ngày và điểm dừng, rồi gửi bản nháp cho chuyên viên tư vấn.",
    tipLead: "Mẹo: dùng ô tìm kiếm để tìm trải nghiệm theo hoạt động, ví dụ",
    tipExample1: "“đi bộ đồi chè”",
    tipOr: "hoặc",
    tipExample2: "“thăm chùa”",
    days: () => "Ngày",
    countries: () => "Quốc gia",
    kmByRoadShort: "km đường bộ",
    reorderFailed: "Chúng tôi chưa lưu được thứ tự mới nên các ngày đã trở lại như cũ. Bạn hãy thử lại.",
    orderSaved: "Đã lưu thứ tự mới.",
    undo: "Hoàn tác",
    tourDay: (from: number, to: number) => `ngày ${from}${to !== from ? `–${to}` : ""} của tour`,
    transfer: "di chuyển",
    gap: (from: string, to: string, reason: string) =>
      `${from} → ${to}: ${reason}. Chuyên viên tư vấn có thể gợi ý chặng nối, hoặc bạn chọn các điểm trên cùng tuyến.`,
    tooLong: (days: string) => `Chuyến đi này dài ${days} — khá dài. Bạn vẫn có thể thêm, nhưng nên rút gọn để hành trình nhẹ nhàng hơn.`,
    gettingThere: "Đến nơi",
    flyInto: "Bay đến",
    thenTransfer: (hint: string) => `, sau đó ${hint}`,
    aboutKm: (km: number) => ` (cách khoảng ${km} km)`,
    toFirstStop: " tới điểm dừng đầu tiên.",
    headingHome: "Về nhà",
    afterLastStop: (hint: string) => `Sau điểm dừng cuối, ${hint}`,
    backTo: " quay lại ",
    flightHome: " để bay về.",
    hintAirport: "bạn đang ở ngay cạnh sân bay",
    hintTransfer: "đi xe 4WD riêng hoặc thuyền",
    hintDrive: "đi đường bộ ngắm cảnh hoặc một chặng bay nội địa",
    hintFlight: "bay nội địa rồi đi tiếp đường bộ",
    yourAddition: "(bạn thêm, không thuộc tour)",
    removeLower: "xoá",
    addFreeDay: "+ ngày tự do",
    addEndDay: "+ thêm ngày ở cuối",
    kindExtraNight: "Thêm đêm",
    kindFreeDay: "Ngày tự do",
    kindEndDay: "Thêm ngày ở cuối",
    reorderHelp:
      "Kéo tay nắm, hoặc dùng phím mũi tên trên đó, để sắp xếp lại ngày và điểm dừng. Khoảng cách chỉ là ước tính và " +
      "cách di chuyển là gợi ý — chuyên viên tư vấn sẽ sắp xếp xe, thuyền và chuyến bay thật cho hành trình.",
    alsoLike: "Có thể bạn cũng thích",
    basedOnPins: "Dựa trên những gì bạn đã ghim.",
    narrationTitle: "Mô tả từng ngày",
    composing: "Đang soạn…",
    regenerate: "Soạn lại",
    compose: "✨ Soạn",
    dayN: (n: number) => `Ngày ${n}`,
    sentToAgency: "✓ Đã gửi cho chuyên viên tư vấn",
    sendTo: (agency: string) => `Gửi cho ${agency}`,
    sendToAdvisor: "Gửi cho chuyên viên tư vấn",
    confirmsLogistics: "Chuyên viên tư vấn xác nhận mọi chi tiết trước khi đặt dịch vụ.",
    poweredBy: "Vận hành bởi",
    yourAgency: "đại lý du lịch của bạn",
  },
);

export const DAYS = defineMessages(
  {
    moveUp: "Move up",
    moveDown: "Move down",
    moveFirst: "Move to start",
    moveLast: "Move to end",
    unitStop: "stop",
    unitDay: "day",
    reorderNamed: (label: string) => `Reorder ${label}`,
    moveNamed: (label: string) => `Move ${label}`,
    showOnMap: "Show on map",
    hideActivities: "Hide activities",
    activitiesHere: "Activities here",
    addExtraNight: "+ extra night",
    extraNightHere: "Extra night here",
    yourAddition: "(your addition)",
    removeLower: "remove",
    route: "Route",
    remove: "Remove",
    removeNamed: (label: string) => `Remove ${label}`,
    reorderHint:
      "Drag the handle, or press the up and down arrow keys on it, to move it. Home and End move it to the start or " +
      "the end. Tap the handle, or press Enter on it, for a menu of moves.",
    yourDays: "Your days",
    dayN: (n: number) => `Day ${n}`,
    stopsAt: (n: number, place: string) => `${n} stops · ${place}`,
  },
  {
    moveUp: "Lên trên",
    moveDown: "Xuống dưới",
    moveFirst: "Lên đầu",
    moveLast: "Xuống cuối",
    unitStop: "điểm dừng",
    unitDay: "ngày",
    reorderNamed: (label: string) => `Sắp xếp lại ${label}`,
    moveNamed: (label: string) => `Di chuyển ${label}`,
    showOnMap: "Xem trên bản đồ",
    hideActivities: "Ẩn hoạt động",
    activitiesHere: "Hoạt động ở đây",
    addExtraNight: "+ thêm đêm",
    extraNightHere: "Thêm một đêm ở đây",
    yourAddition: "(bạn thêm)",
    removeLower: "xoá",
    route: "Tuyến",
    remove: "Xoá",
    removeNamed: (label: string) => `Xoá ${label}`,
    reorderHint:
      "Kéo tay nắm, hoặc nhấn phím mũi tên lên và xuống trên đó, để di chuyển. Phím Home và End đưa về đầu hoặc " +
      "cuối. Chạm vào tay nắm, hoặc nhấn Enter trên đó, để mở danh sách thao tác.",
    yourDays: "Các ngày của bạn",
    dayN: (n: number) => `Ngày ${n}`,
    stopsAt: (n: number, place: string) => `${n} điểm dừng · ${place}`,
  },
);

export const LEAD = defineMessages(
  {
    intro: (agency: string) => `Send this trip to ${agency}. They plan it with you and confirm every detail.`,
    fullName: "Full name",
    email: "Email",
    phoneHint: "Phone with country code, optional (e.g. +44 20 7946 0000)",
    phoneTitle: "Start with + and your country code, for example +44 for the United Kingdom",
    adults: "Adults",
    childAges: "Children's ages",
    childAgesExample: "e.g. 4, 9",
    startDate: "Start date (optional)",
    budget: "Budget per person",
    budgetChoose: "Choose (optional)",
    budget_under_1000: "Under 1,000 USD",
    budget_1000_2000: "1,000 to 2,000 USD",
    budget_2000_3500: "2,000 to 3,500 USD",
    budget_3500_5000: "3,500 to 5,000 USD",
    budget_over_5000: "Over 5,000 USD",
    budget_not_sure: "Not sure yet",
    messageLabel: "Message (optional)",
    messagePlaceholder: "Anything the agency should know (optional)",
    consent: (agency: string) => `${agency} may contact me about this trip.`,
    marketing: (agency: string) => `Send me travel ideas from ${agency} (optional).`,
    privacy: (agency: string) =>
      `Your details go only to ${agency}, who answers this request. The planner keeps no copy once it is delivered.`,
    howUsed: "How your details are used:",
    draft: "(draft)",
    sending: "Sending…",
    send: "Send my request",
    // lib/leadForm.ts leadResultMessage
    sent: (agency: string) => `Sent to ${agency}. They will contact you about this trip.`,
    consentRequired: "Please tick the box so the agency may contact you.",
    invalidLead: "Some answers need a look: please check the highlighted fields. A phone starts with + and the country code.",
    unavailable: (agency: string) => `${agency} cannot take requests from the planner right now. Please contact them directly.`,
    emptyTrip: "Add a stop or a day to your trip first.",
    rateLimited: "You have sent several requests already. Please wait a little before sending again.",
    capReached: (agency: string) =>
      `${agency} has received many requests from the planner today. Please try again tomorrow or contact them directly.`,
    botCheck: "We could not confirm this request came from a browser. Please reload the page and try again.",
    failed: "Something went wrong. Please try again.",
  },
  {
    intro: (agency: string) => `Gửi chuyến đi này cho ${agency}. Họ sẽ cùng bạn lên kế hoạch và xác nhận mọi chi tiết.`,
    fullName: "Họ và tên",
    email: "Email",
    phoneHint: "Số điện thoại kèm mã quốc gia, không bắt buộc (ví dụ +84 90 123 4567)",
    phoneTitle: "Bắt đầu bằng + và mã quốc gia, ví dụ +84 cho Việt Nam",
    adults: "Người lớn",
    childAges: "Tuổi của trẻ em",
    childAgesExample: "ví dụ 4, 9",
    startDate: "Ngày khởi hành (không bắt buộc)",
    budget: "Ngân sách mỗi người",
    budgetChoose: "Chọn (không bắt buộc)",
    budget_under_1000: "Dưới 1.000 USD",
    budget_1000_2000: "1.000 đến 2.000 USD",
    budget_2000_3500: "2.000 đến 3.500 USD",
    budget_3500_5000: "3.500 đến 5.000 USD",
    budget_over_5000: "Trên 5.000 USD",
    budget_not_sure: "Chưa chắc chắn",
    messageLabel: "Lời nhắn (không bắt buộc)",
    messagePlaceholder: "Điều đại lý nên biết (không bắt buộc)",
    consent: (agency: string) => `${agency} có thể liên hệ với tôi về chuyến đi này.`,
    marketing: (agency: string) => `Gửi cho tôi gợi ý du lịch từ ${agency} (không bắt buộc).`,
    privacy: (agency: string) =>
      `Thông tin của bạn chỉ được gửi tới ${agency}, nơi trả lời yêu cầu này. Trang lập kế hoạch không giữ bản sao nào sau khi đã gửi.`,
    howUsed: "Cách thông tin của bạn được sử dụng:",
    draft: "(bản nháp)",
    sending: "Đang gửi…",
    send: "Gửi yêu cầu của tôi",
    sent: (agency: string) => `Đã gửi cho ${agency}. Họ sẽ liên hệ với bạn về chuyến đi này.`,
    consentRequired: "Bạn hãy đánh dấu ô đồng ý để đại lý có thể liên hệ với bạn.",
    invalidLead: "Một vài câu trả lời cần xem lại: hãy kiểm tra các ô được đánh dấu. Số điện thoại bắt đầu bằng + và mã quốc gia.",
    unavailable: (agency: string) => `${agency} hiện chưa nhận được yêu cầu từ trang lập kế hoạch. Bạn hãy liên hệ trực tiếp với họ.`,
    emptyTrip: "Hãy thêm một điểm dừng hoặc một ngày vào chuyến đi trước.",
    rateLimited: "Bạn đã gửi khá nhiều yêu cầu. Hãy đợi một lúc rồi gửi lại.",
    capReached: (agency: string) =>
      `${agency} đã nhận nhiều yêu cầu từ trang lập kế hoạch hôm nay. Bạn hãy thử lại vào ngày mai hoặc liên hệ trực tiếp với họ.`,
    botCheck: "Chúng tôi chưa xác nhận được yêu cầu này đến từ trình duyệt. Hãy tải lại trang và thử lại.",
    failed: "Đã có lỗi xảy ra. Bạn hãy thử lại.",
  },
);

export const FILTERS = defineMessages(
  {
    search: "Search experiences",
    searchPlaceholder: "Search experiences — e.g. sunrise trek, street food, temples",
    clearSearch: "Clear search",
    noMatches: "No matches — try different words or clear a filter.",
    matches: (n: number, text: string) => `${n} ${n_(n, "place", "places")} match “${text}”, best first.`,
    backToMap: "Back to map",
    allCountries: "All countries",
    activityOptional: "(optional — or pick a country first)",
    anyIntensity: "Any intensity",
    seasonMonth: "Season month",
    anyMonth: "Any month",
    clearAll: "Clear all",
    filters: "Filters",
    country: "Country",
    activity: "Activity",
    intensity: "Intensity",
    month: (i: number) => ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][i] ?? "",
    // lib/types.ts ACTIVITIES / INTENSITIES (the English label is the value with spaces)
    value: (v: string) => v.replace(/_/g, " "),
  },
  {
    search: "Tìm trải nghiệm",
    searchPlaceholder: "Tìm trải nghiệm — ví dụ leo núi ngắm bình minh, ẩm thực đường phố, chùa chiền",
    clearSearch: "Xoá tìm kiếm",
    noMatches: "Không có kết quả — hãy thử từ khác hoặc bỏ bớt bộ lọc.",
    matches: (n: number, text: string) => `${n} địa điểm khớp với “${text}”, phù hợp nhất trước.`,
    backToMap: "Quay lại bản đồ",
    allCountries: "Tất cả quốc gia",
    activityOptional: "(không bắt buộc — hoặc chọn quốc gia trước)",
    anyIntensity: "Mọi mức độ",
    seasonMonth: "Tháng đi",
    anyMonth: "Tháng bất kỳ",
    clearAll: "Xoá tất cả",
    filters: "Bộ lọc",
    country: "Quốc gia",
    activity: "Hoạt động",
    intensity: "Mức độ",
    month: (i: number) => (i >= 0 && i < 12 ? `Th${i + 1}` : ""),
    value: (v: string) => VALUE_VI[v] ?? v.replace(/_/g, " "),
  },
);

export const ROUTES = defineMessages(
  {
    startFrom: "Start from a tour route",
    intro: "Routes built only from real tours — pick one, then make it yours.",
    planningFor: (n: number) => ` Planning for ${n} ${n_(n, "traveller", "travellers")}.`,
    headingTo: "Heading to",
    headingToOption: "Heading to…",
    finding: "Finding…",
    find: "Find routes",
    none: (days: string, country: string) => `No tour route of about ${days} in ${country} yet — try another length.`,
    adding: "Adding…",
    startWith: "Start with this route",
    planFromRoute: "Plan from a tour route",
    close: "Close",
    days: "Days",
  },
  {
    startFrom: "Bắt đầu từ tuyến của một tour",
    intro: "Các tuyến chỉ dựng từ tour có thật — chọn một tuyến rồi chỉnh theo ý bạn.",
    planningFor: (n: number) => ` Lên kế hoạch cho ${n} người.`,
    headingTo: "Điểm đến",
    headingToOption: "Bạn muốn đi đâu…",
    finding: "Đang tìm…",
    find: "Tìm tuyến",
    none: (days: string, country: string) => `Chưa có tuyến tour khoảng ${days} ở ${country} — hãy thử độ dài khác.`,
    adding: "Đang thêm…",
    startWith: "Bắt đầu với tuyến này",
    planFromRoute: "Lên kế hoạch từ tuyến của tour",
    close: "Đóng",
    days: "Số ngày",
  },
);

export const SHARE = defineMessages(
  {
    title: "My trip",
    share: "Share link",
    print: "Print view",
    copied: "Link copied. Anyone with it sees this trip and cannot change it.",
    copyFailed: "We couldn't copy the link. Open the print view and copy its address instead.",
    readOnly: "A read-only link: it shows the places of this trip, nothing about you.",
  },
  {
    title: "Chuyến đi của tôi",
    share: "Chia sẻ liên kết",
    print: "Bản in",
    copied: "Đã sao chép liên kết. Ai có liên kết đều xem được chuyến đi này nhưng không sửa được.",
    copyFailed: "Chúng tôi chưa sao chép được liên kết. Hãy mở bản in và sao chép địa chỉ của nó.",
    readOnly: "Liên kết chỉ để xem: chỉ hiện các địa điểm của chuyến đi, không có thông tin về bạn.",
  },
);

export const POPUP = defineMessages(
  {
    loading: "Loading experiences",
    errorTitle: "We couldn't load this place",
    errorBody: "Close it and pick the marker again, or try another place.",
    none: "No experiences catalogued here yet.",
    inTrip: (day: number) => `In your trip — Day ${day}`,
    add: "+ Add to trip",
    remove: "Remove",
  },
  {
    loading: "Đang tải trải nghiệm",
    errorTitle: "Chúng tôi chưa tải được địa điểm này",
    errorBody: "Hãy đóng lại và chọn lại điểm trên bản đồ, hoặc thử địa điểm khác.",
    none: "Chưa có trải nghiệm nào ở đây.",
    inTrip: (day: number) => `Đã có trong chuyến đi — Ngày ${day}`,
    add: "+ Thêm vào chuyến đi",
    remove: "Xoá",
  },
);

export const TOURS = defineMessages(
  {
    toursIn: (country: string) => `Tours in ${country}`,
    allTours: "All tours",
    travelIn: "Travel in",
    anyMonth: "Any month",
    shortest: "Shortest first",
    longest: "Longest first",
    loadError: "We couldn't load tours right now.",
    tryAgain: "Try again",
    routeError: "Couldn't load this route. Try again.",
    notOnMap: "Not on the planner map yet — ask an advisor.",
    list: "Tours list",
    changeCountry: "Change the country on the Map view.",
    sort: "Sort",
    loading: "Loading tours…",
    noneInMonth: (country: string | null, month: string) =>
      `No tours ${country ? `in ${country} ` : ""}list ${month} as a good time to go. Try another month.`,
    none: (country: string | null) => `No tours ${country ? `in ${country} ` : ""}yet. Try another country.`,
    addQuestion: "Add to your current trip?",
    addRoute: (days: string) => `Add its route (${days}) to your current trip?`,
    addItsRoute: "Add its route",
    notNow: "Not now",
    adding: "Adding…",
    planRoute: "Plan this route",
    loadingMore: "Loading…",
    showMore: (shown: number, total: number) => `Show more (${shown} of ${total})`,
    moreError: "Couldn't load more tours. Try again.",
    month: (i: number) =>
      ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
        "December"][i] ?? "",
  },
  {
    toursIn: (country: string) => `Tour ở ${country}`,
    allTours: "Tất cả tour",
    travelIn: "Đi vào",
    anyMonth: "Tháng bất kỳ",
    shortest: "Ngắn nhất trước",
    longest: "Dài nhất trước",
    loadError: "Chúng tôi chưa tải được danh sách tour lúc này.",
    tryAgain: "Thử lại",
    routeError: "Chưa tải được tuyến này. Hãy thử lại.",
    notOnMap: "Chưa có trên bản đồ — hãy hỏi chuyên viên tư vấn.",
    list: "Danh sách tour",
    changeCountry: "Đổi quốc gia ở chế độ Bản đồ.",
    sort: "Sắp xếp",
    loading: "Đang tải tour…",
    noneInMonth: (country: string | null, month: string) =>
      `Chưa có tour nào ${country ? `ở ${country} ` : ""}ghi ${month} là thời điểm đẹp. Hãy thử tháng khác.`,
    none: (country: string | null) => `Chưa có tour nào ${country ? `ở ${country}` : ""}. Hãy thử quốc gia khác.`,
    addQuestion: "Thêm vào chuyến đi hiện tại?",
    addRoute: (days: string) => `Thêm tuyến của tour (${days}) vào chuyến đi hiện tại?`,
    addItsRoute: "Thêm tuyến",
    notNow: "Để sau",
    adding: "Đang thêm…",
    planRoute: "Lên kế hoạch theo tuyến này",
    loadingMore: "Đang tải…",
    showMore: (shown: number, total: number) => `Xem thêm (${shown} / ${total})`,
    moreError: "Chưa tải thêm được tour. Hãy thử lại.",
    month: (i: number) => (i >= 0 && i < 12 ? `tháng ${i + 1}` : ""),
  },
);

export const PREFILL = defineMessages(
  {
    thisTour: "this tour",
    confirm: (tour: string, days: string) => `Add ${tour} (${days}) to your current trip?`,
    addItsRoute: "Add its route",
    notNow: "Not now",
    applied: (tour: string, places: boolean) =>
      `${tour} is on your trip${places ? ", built from its places and our experiences there" : ""}. Make it yours, ` +
      "then send it to an advisor.",
    missing: (tour: string, country: string, days: string | null) =>
      `${tour} isn't on the planner map yet. We've set ${country}${days ? ` and ${days}` : ""} in the route finder — ` +
      "pick a route, or send this tour to an advisor to tailor it.",
    thisTourCap: "This tour",
    itsCountry: "its country",
    dayN: (n: number) => `Day ${n}`,
    error: "We couldn't load this tour's route. Try the route finder instead.",
    ok: "OK",
  },
  {
    thisTour: "tour này",
    confirm: (tour: string, days: string) => `Thêm ${tour} (${days}) vào chuyến đi hiện tại?`,
    addItsRoute: "Thêm tuyến",
    notNow: "Để sau",
    applied: (tour: string, places: boolean) =>
      `${tour} đã có trong chuyến đi${places ? ", dựng từ các địa điểm của tour và trải nghiệm ở đó" : ""}. Hãy ` +
      "chỉnh theo ý bạn rồi gửi cho chuyên viên tư vấn.",
    missing: (tour: string, country: string, days: string | null) =>
      `${tour} chưa có trên bản đồ. Chúng tôi đã chọn sẵn ${country}${days ? ` và ${days}` : ""} trong công cụ tìm ` +
      "tuyến — hãy chọn một tuyến, hoặc gửi tour này cho chuyên viên tư vấn để điều chỉnh.",
    thisTourCap: "Tour này",
    itsCountry: "quốc gia của tour",
    dayN: (n: number) => `Ngày ${n}`,
    error: "Chúng tôi chưa tải được tuyến của tour này. Hãy dùng công cụ tìm tuyến.",
    ok: "OK",
  },
);

/** "N day(s)" in the planner's language (lib/text.ts formatDays is the English one). */
export function formatDaysIn(n: number, lang: Lang): string {
  return lang === "vi" ? `${n} ngày` : `${n} ${n === 1 ? "day" : "days"}`;
}

/** The planner language of a CIS locale ("vi" or anything else = English). */
export function plannerLang(locale: string | null | undefined): Lang {
  return locale === "vi" ? "vi" : "en";
}

/** Every catalogue of the planner (the run-time key check of lib/plannerI18n.test.ts). */
export const PLANNER_CATALOGS: Record<string, Messages<Catalog>> = {
  PLANNER, TRIP, DAYS, LEAD, FILTERS, ROUTES, SHARE, POPUP, TOURS, PREFILL, ADDONS,
} as unknown as Record<string, Messages<Catalog>>;

export { translator, type Lang, type Translator };
