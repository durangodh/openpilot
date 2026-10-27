// Renders the APM launcher PNGs from icon_foreground.svg (run: node render_icons.js; needs playwright).
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const BG = '#0D1117';
const DENSITIES = { mdpi: 1, hdpi: 1.5, xhdpi: 2, xxhdpi: 3, xxxhdpi: 4 };
const svg = fs.readFileSync(path.join(__dirname, 'icon_foreground.svg'), 'utf8');
const inner = svg.replace(/^[\s\S]*?<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '');

// Legacy icons show the centre 88 of the 108 canvas so the whole window fits with a margin.
const page = (size, view, shape, background) => `<html><body style="margin:0;background:transparent">
<div style="width:${size}px;height:${size}px;overflow:hidden;border-radius:${shape};background:${background}">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="${view}" width="${size}" height="${size}">${inner}</svg></div></body></html>`;

(async () => {
  const browser = await chromium.launch();
  const tab = await browser.newPage();
  for (const [density, scale] of Object.entries(DENSITIES)) {
    const dir = path.join(__dirname, 'res', `mipmap-${density}`);
    fs.mkdirSync(dir, { recursive: true });
    const outputs = [
      ['ic_launcher.png', 48 * scale, '10 10 88 88', '22%', BG],
      ['ic_launcher_round.png', 48 * scale, '10 10 88 88', '50%', BG],
      ['ic_launcher_foreground.png', 108 * scale, '0 0 108 108', '0', 'transparent'],
      ['ic_launcher_background.png', 108 * scale, '0 0 108 108', '0', BG],
    ];
    for (const [name, size, view, shape, background] of outputs) {
      const html = name === 'ic_launcher_background.png'
        ? `<html><body style="margin:0"><div style="width:${size}px;height:${size}px;background:${BG}"></div></body></html>`
        : page(size, view, shape, background);
      await tab.setViewportSize({ width: size, height: size });
      await tab.setContent(html);
      await tab.screenshot({ path: path.join(dir, name), omitBackground: true });
    }
  }
  await browser.close();
})();
