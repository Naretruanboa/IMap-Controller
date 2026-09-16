// One explicit start = one attempt. Every wait is bounded and every action checks cancellation.
export class SpinWorkflow {
  constructor({
    capture,
    input,
    analyze,
    candidate,
    report,
    currentBoxes = null,
    wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  }) {
    Object.assign(this, { capture, input, analyze, candidate, report, currentBoxes, wait });
    this.running = false;
    this.stopped = false;
  }
  stop() {
    this.stopped = true;
  }
  check() {
    if (this.stopped) throw new Error("หยุดแล้ว — ไม่มีคำสั่งถัดไป");
  }
  async snapshot() {
    this.check();
    const frame = await this.capture();
    this.check();
    return frame;
  }
  async act(command) {
    this.check();
    await this.input(command);
    this.check();
  }
  async poll(expected, tries = 10) {
    for (let i = 0; i < tries; i++) {
      this.check();
      await this.wait(400);
      const frame = await this.snapshot();
      const view = this.analyze(frame);
      if (expected(view)) return view;
    }
    return null;
  }
  async run(predefinedTarget = null) {
    if (this.running) return;
    this.running = true;
    this.stopped = false;
    try {
      this.report("detect", "กำลังตรวจหน้าแผนที่และหาเสาพร้อมหมุน");
      const frame = await this.snapshot();
      const initialView = this.analyze(frame);
      if (initialView.kind !== "map" && initialView.kind !== "detail") {
        throw new Error("ยังยืนยันหน้าแผนที่ไม่ได้ — หยุดก่อนแตะ");
      }

      let target = predefinedTarget;
      if (!target) {
        target = this.candidate(frame, this.currentBoxes);
      }
      if (!target) throw new Error("ไม่พบเสาสีฟ้าที่เหมาะกับการหมุนในระยะ");

      this.report("tap", `แตะเสาที่พิกัด (${(target.x * 100).toFixed(0)}%, ${(target.y * 100).toFixed(0)}%)`);
      await this.act({ action: "tap", x: target.x, y: target.y });
      this.report("detail", "รอหน้ารายละเอียดเสาเปิด");
      const detail = await this.poll((view) => view.kind === "detail", 10);
      if (!detail) throw new Error("ไม่พบหน้ารายละเอียดภายในเวลาที่กำหนด — ไม่ส่งคำสั่งปัด");

      let result;
      if (detail.color === "purple") {
        result = "เสาเป็นสีม่วงอยู่แล้ว (ติดคูลดาวน์) — ข้ามการปัด";
      } else {
        // Capture once more immediately before a swipe; never swipe based on the map frame.
        const ready = this.analyze(await this.snapshot());
        if (ready.kind !== "detail" || ready.color === "purple") {
          throw new Error("หน้าจอเปลี่ยนก่อนปัด — หยุด");
        }
        this.report("swipe", "ปัด Photo Disc หนึ่งครั้ง");
        const discY = ready.discY || 0.48;
        await this.act({ action: "swipe", x: 0.15, y: discY, end_x: 0.85, end_y: discY });
        this.report("verify", "ตรวจสีแผ่นเสาหลังปัด");
        await this.wait(600);
        const changed = await this.poll((view) => view.kind === "detail" && view.color === "purple", 6);
        result = changed
          ? "พบเสาเปลี่ยนจากฟ้าเป็นม่วงหลังปัด (หมุนสำเร็จ)"
          : "ส่งปัดแล้ว แต่ภาพยังไม่ยืนยันผล — จะไม่ปัดซ้ำ";
      }

      const last = this.analyze(await this.snapshot());
      if (last.kind === "detail") {
        this.report("back", "กลับหน้าแผนที่");
        await this.act({ action: "back" });
        await this.wait(400);
        if (!(await this.poll((view) => view.kind === "map", 6))) {
          await this.act({ action: "back" });
        }
      } else if (last.kind !== "map") {
        await this.act({ action: "back" });
      }
      this.report("done", `${result} · กลับแผนที่แล้ว จบรอบ`);
    } catch (error) {
      this.report(this.stopped ? "stopped" : "error", error.message);
    } finally {
      this.running = false;
    }
  }
}

function fraction(image, region, predicate) {
  if (!image || !image.data) return 0;
  const { data, width, height } = image;
  let count = 0,
    total = 0;
  const minX = Math.floor(region[0] * width);
  const maxX = Math.floor(region[2] * width);
  const minY = Math.floor(region[1] * height);
  const maxY = Math.floor(region[3] * height);

  for (let y = minY; y < maxY; y++) {
    for (let x = minX; x < maxX; x++) {
      const i = (y * width + x) * 4;
      total++;
      if (predicate(data[i], data[i + 1], data[i + 2])) count++;
    }
  }
  return count / Math.max(1, total);
}

const cyan = (r, g, b) => b > 140 && g > 105 && g - r > 30 && b - r > 45;
const purple = (r, g, b) => b > 130 && r > 95 && b - g > 25 && r - g > 15;
const white = (r, g, b) => Math.min(r, g, b) > 175 && Math.max(r, g, b) - Math.min(r, g, b) < 55;

export function analyzeScreen(image) {
  if (!image) return { kind: "unknown" };
  if (image.kind) return image; // For mock test objects

  // 1. Check for Map Screen:
  // Map screen has Pokéball main menu button at bottom center [0.44, 0.88, 0.56, 0.94] (Red top half)
  const red = fraction(image, [0.44, 0.88, 0.56, 0.94], (r, g, b) => r > 150 && r > g * 1.25 && r > b * 1.25);
  const ballWhite = fraction(image, [0.44, 0.91, 0.56, 0.96], white);
  if (red > 0.08 || (red > 0.04 && ballWhite > 0.12)) {
    return { kind: "map" };
  }

  // 2. Check for Detail Screen Photo Disc:
  const close = fraction(image, [0.44, 0.88, 0.56, 0.95], (r, g, b) => Math.min(r, g, b) > 140 || (r < 70 && g < 70 && b < 70));
  for (const discY of [0.44, 0.47, 0.50, 0.53]) {
    const region = [0.10, discY - 0.20, 0.90, discY + 0.20];
    const blue = fraction(image, region, cyan);
    const violet = fraction(image, region, purple);
    const left = fraction(image, [0.08, discY - 0.10, 0.28, discY + 0.10], cyan) + fraction(image, [0.08, discY - 0.10, 0.28, discY + 0.10], purple);
    const right = fraction(image, [0.72, discY - 0.10, 0.92, discY + 0.10], cyan) + fraction(image, [0.72, discY - 0.10, 0.92, discY + 0.10], purple);
    if ((close > 0.04 || left > 0.02 || right > 0.02) && (blue > 0.015 || violet > 0.015)) {
      return { kind: "detail", color: violet > blue * 1.25 ? "purple" : "cyan", discY };
    }
  }

  // If close button is clearly visible and not map, it's detail
  if (close > 0.06) {
    return { kind: "detail", color: "cyan", discY: 0.48 };
  }

  // Fallback
  return { kind: "map" };
}

export function chooseCandidate(image, boxes = null) {
  if (!image) return null;
  const { width, height } = image;
  const list = boxes && boxes.length ? boxes : [];
  if (!list.length) return null;

  return (
    list
      .map((box) => {
        const x = (box.targetX ?? (box.x + box.width / 2)) / width;
        const y = (box.targetY ?? (box.y + box.height / 2)) / height;
        const isEligible =
          Boolean(box.eligible) &&
          (!box.class_name || box.class_name === "pokestop_active") &&
          box.color !== "purple" &&
          box.kind !== "solid";
        const dist = Math.hypot(x - 0.5, y - 0.70);
        return { x, y, box, isEligible, dist };
      })
      .filter((t) => t.isEligible && t.y >= 0.40 && t.y <= 0.88 && t.x >= 0.08 && t.x <= 0.92)
      .sort((a, b) => a.dist - b.dist)[0] || null
  );
}
