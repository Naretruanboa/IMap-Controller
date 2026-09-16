// Accurate Pokestop Photo Disc detector: detects expanded spinnable discs vs distant solid cubes/gyms
export function detectStops({ data, width, height }, sensitivity = 50) {
  const mask = new Uint8Array(width * height);
  const relaxed = sensitivity / 100;
  
  const startY = Math.floor(height * 0.30), endY = Math.floor(height * 0.88);
  const startX = Math.floor(width * 0.05), endX = Math.floor(width * 0.95);

  for (let y = startY; y < endY; y++) {
    for (let x = startX; x < endX; x++) {
      const i = y * width + x, p = i * 4;
      const r = data[p], g = data[p + 1], b = data[p + 2];
      
      // Real Pokémon GO cyan Photo Disc rim/core
      const cyan = (b >= 170 - relaxed * 10) && (r <= 115 + relaxed * 10) && 
                   (b - r >= 85 - relaxed * 10) && (b - g >= 15 - relaxed * 5) && (g >= 90);
                   
      // Spun purple Photo Disc
      const purple = (b >= 145 - relaxed * 20) && (r >= 115 - relaxed * 20) &&
                     (b - g >= 30 - relaxed * 15) && (r - g >= 20 - relaxed * 10);

      if (cyan || purple) mask[i] = cyan ? 1 : 2;
    }
  }

  const visited = new Uint8Array(width * height);
  const queue = new Int32Array(width * height);
  const boxes = [];
  const scale = (width * height) / (900 * 1600);
  const minPixels = Math.max(15, Math.round(750 * scale - relaxed * 300 * scale));
  const minDim = Math.max(8, Math.round(28 * (width / 900)));
  const maxDim = Math.round(220 * (width / 900));

  for (let i = 0; i < mask.length; i++) {
    if (!mask[i] || visited[i]) continue;
    let head = 0, tail = 1;
    let minX = width, minY = height, maxX = 0, maxY = 0;
    let sumX = 0, sumY = 0;
    let blueCount = 0, purpleCount = 0, glowCount = 0;
    
    queue[0] = i; visited[i] = 1;

    while (head < tail) {
      const n = queue[head++];
      const x = n % width, y = Math.floor(n / width);
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      sumX += x; sumY += y;

      const p = n * 4;
      const r = data[p], g = data[p + 1], b = data[p + 2];
      if ((b >= 205 && b - r >= 115) || (r >= 165 && b >= 180)) glowCount++;
      if (mask[n] === 1) blueCount++; else purpleCount++;

      // Standard 8-connectivity
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (dx === 0 && dy === 0) continue;
          const nx = x + dx, ny = y + dy;
          if (nx >= startX && nx < endX && ny >= startY && ny < endY) {
            const next = ny * width + nx;
            if (mask[next] && !visited[next]) {
              visited[next] = 1;
              queue[tail++] = next;
            }
          }
        }
      }
    }

    const w = maxX - minX + 1, h = maxY - minY + 1;
    const aspect = w / h;
    const fill = tail / (w * h);
    const glowRatio = glowCount / tail;
    const cx = Math.round(sumX / tail), cy = Math.round(sumY / tail);

    // Identify distant solid diamonds/cubes (aspect ~ 1.0, diamond fill ~ 0.50, or square fill > 0.65)
    const isSolidCube = (aspect >= 0.70 && aspect <= 1.30 && fill >= 0.40);
    const isGymOrHuge = (w > maxDim || h > maxDim || tail > Math.round(9000 * scale));
    const isNoise = (tail < minPixels || Math.max(w, h) < minDim);

    const isRing = !isSolidCube && !isGymOrHuge && !isNoise && (glowRatio >= 0.12 || tail < 100);
    const color = blueCount >= purpleCount ? 'cyan' : 'purple';

    const score = isRing 
      ? Math.min(99, Math.round(80 + 15 * Math.min(1, glowRatio / 0.4) + 4 * Math.min(1, tail / (1500 * scale))))
      : Math.min(38, Math.round(15 + 20 * fill));

    const kind = isRing ? 'ring' : 'solid';
    const reason = isRing 
      ? `จานหมุนเสา (${color === 'cyan' ? 'สีฟ้าพร้อมหมุน' : 'สีม่วง'}) · ขนาด ${w}×${h} px`
      : (isSolidCube ? 'เสาสี่เหลี่ยม/ลูกบาศก์ระยะไกล' : 'วัตถุอื่นนอกเกณฑ์จานหมุน');

    boxes.push({
      x: minX, y: minY, width: w, height: h, pixels: tail,
      kind, color, score, reason,
      targetX: cx, targetY: cy,
      eligible: kind === 'ring' && color === 'cyan' && score >= 80
    });
  }

  // Deduplicate and sort top-to-bottom
  const filtered = [];
  const sorted = boxes.filter(b => b.kind === 'ring' || b.pixels > 30).sort((a, b) => b.score - a.score || b.pixels - a.pixels);
  for (const box of sorted) {
    const isOverlapping = filtered.some(f => 
      Math.abs(f.targetX - box.targetX) < Math.max(f.width, box.width) * 0.5 &&
      Math.abs(f.targetY - box.targetY) < Math.max(f.height, box.height) * 0.5
    );
    if (!isOverlapping) {
      filtered.push(box);
    }
  }

  return filtered.slice(0, 40).sort((a, b) => a.y - b.y);
}


