// AA-1152 (S285 advisory PR-29 T6, B2 option C) — the words of the AA add-ons on each day card, English and
// Vietnamese (the kit's typed catalogue, like lib/planner.messages.ts; lib/plannerI18n.test.ts checks both sides).
// The agency's name comes in as an argument; no message names Adventure Asia (lib/agencyBrand.test.ts).
import { defineMessages } from "../ui/core/i18n.ts";

const WEEKDAYS_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WEEKDAYS_VI = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];

function days(list: number[], names: string[], every: string): string {
  const d = [...new Set(list)].filter((x) => x >= 1 && x <= 7).sort((a, b) => a - b);
  return d.length === 7 ? every : d.map((x) => names[x - 1]).join(", ");
}

export const ADDONS = defineMessages(
  {
    title: "Optional activities this day",
    help: "Tick what you would like to add; your advisor confirms it and its price in your quote.",
    atSleep: "Where you stay",
    runs: (list: number[]) => `Runs ${days(list, WEEKDAYS_EN, "every day")}`,
    starts: (times: string) => `Starts ${times}`,
    minPax: (n: number) => `From ${n} travellers`,
    from: (price: string) => `From ${price} per person`,
    priceNote: (agency: string) => `Indicative price; your final price is in ${agency}'s quote.`,
    yourAgency: "your travel agency",
    add: (title: string) => `Add ${title}`,
    failed: "We could not save that choice. Try again.",
  },
  {
    title: "Hoạt động tuỳ chọn trong ngày",
    help: "Đánh dấu những gì bạn muốn thêm; chuyên viên tư vấn sẽ xác nhận và báo giá trong báo giá của bạn.",
    atSleep: "Nơi bạn nghỉ đêm",
    runs: (list: number[]) => `Hoạt động ${days(list, WEEKDAYS_VI, "hằng ngày")}`,
    starts: (times: string) => `Bắt đầu ${times}`,
    minPax: (n: number) => `Từ ${n} khách`,
    from: (price: string) => `Từ ${price} mỗi khách`,
    priceNote: (agency: string) => `Giá tham khảo; giá cuối cùng theo báo giá của ${agency}.`,
    yourAgency: "đại lý du lịch của bạn",
    add: (title: string) => `Thêm ${title}`,
    failed: "Chưa lưu được lựa chọn này. Hãy thử lại.",
  },
);
