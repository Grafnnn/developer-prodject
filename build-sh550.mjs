import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { once } from 'node:events';
import * as XLSX from 'xlsx';
import archiver from 'archiver';
import fontkit from '@pdf-lib/fontkit';
import { PDFDocument, rgb } from 'pdf-lib';

const OUTPUT = path.resolve('dist');
const WORK = path.resolve('.sh550-work');
fs.rmSync(OUTPUT, { recursive: true, force: true });
fs.rmSync(WORK, { recursive: true, force: true });
fs.mkdirSync(OUTPUT, { recursive: true });
fs.mkdirSync(WORK, { recursive: true });

const WORKING_REGISTRY_ID = '1r6lNVkvetkvNAvdQVUyFpa8PC3Nb-jcD';
const ORIGINAL_REGISTRY_ID = '1JCk8rtTP15rEGMZLOGcQpuQ-7MQH7WO1';
const SCAN_AK_ID = '1Q_6hTMAUUdrSu8BZA4pCIH2-EWv6ktbm';
const RAR_ARCHIVE_ID = '1EyHU0ZISbDJ4jgUTktAhkcNByl8w8-ii';

const ALBUMS = [
  { sheet: 'кронштейны', file: 'SH550_01_Kronshteiny.pdf', zip: 'SH550_01_Kronshteiny_components.zip', title: 'Альбом 01. Кронштейны', expected: 32 },
  { sheet: 'утепление 1 слой', file: 'SH550_02_Uteplenie_1_sloy.pdf', zip: 'SH550_02_Uteplenie_1_sloy_components.zip', title: 'Альбом 02. Утепление — 1-й слой', expected: 30 },
  { sheet: 'утепление 2 слой', file: 'SH550_03_Uteplenie_2_sloy.pdf', zip: 'SH550_03_Uteplenie_2_sloy_components.zip', title: 'Альбом 03. Утепление — 2-й слой', expected: 30 },
  { sheet: 'направляющие', file: 'SH550_04_Napravlyayushchie.pdf', zip: 'SH550_04_Napravlyayushchie_components.zip', title: 'Альбом 04. Направляющие и подсистема НВФ', expected: 39 },
  { sheet: 'керамогранит', file: 'SH550_05_Keramogranit.pdf', zip: 'SH550_05_Keramogranit_components.zip', title: 'Альбом 05. Керамогранит', expected: 64 },
];

const A4 = [595.28, 841.89];
const COLOR = {
  navy: rgb(0.08, 0.22, 0.38),
  blue: rgb(0.85, 0.9, 0.96),
  red: rgb(0.72, 0.02, 0.02),
  orange: rgb(0.96, 0.8, 0.64),
  gray: rgb(0.36, 0.36, 0.36),
  lightGray: rgb(0.94, 0.94, 0.94),
  black: rgb(0, 0, 0),
  white: rgb(1, 1, 1),
};

const logLines = [];
function log(message) {
  const line = `[${new Date().toISOString()}] ${message}`;
  console.log(line);
  logLines.push(line);
}

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function safeName(value, max = 118) {
  const s = String(value ?? '').replace(/[\\/:*?"<>|\r\n]+/g, '_').replace(/\s+/g, ' ').trim().replace(/[ ._]+$/g, '');
  return (s || 'document').slice(0, max);
}
function normalize(value) {
  return String(value ?? '').toLowerCase().replaceAll('ё', 'е').replace(/[^0-9a-zа-я]+/gi, ' ').replace(/\s+/g, ' ').trim();
}
function splitList(value) { return String(value ?? '').split(/\s*;\s*/).map(s => s.trim()).filter(Boolean); }
function extractDriveIds(value) {
  return splitList(value).map(item => {
    const m1 = item.match(/\/d\/([A-Za-z0-9_-]+)/);
    const m2 = item.match(/[?&]id=([A-Za-z0-9_-]+)/);
    return m1?.[1] || m2?.[1] || '';
  }).filter(Boolean);
}
function hash(buffer) { return crypto.createHash('sha256').update(buffer).digest('hex'); }
function csvEscape(value) {
  const s = String(value ?? '');
  return /[",\r\n;]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

async function fetchBufferFromUrl(url, label, retries = 4) {
  let lastError;
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const response = await fetch(url, {
        redirect: 'follow',
        headers: {
          'User-Agent': 'Mozilla/5.0 SH550-PTO-Builder/1.0',
          'Accept': '*/*',
        },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status} ${response.statusText}`);
      const arrayBuffer = await response.arrayBuffer();
      const buffer = Buffer.from(arrayBuffer);
      if (buffer.length < 80) throw new Error(`слишком короткий ответ: ${buffer.length} байт`);
      const head = buffer.subarray(0, 500).toString('utf8').toLowerCase();
      if (head.includes('<html') || head.includes('<!doctype html')) throw new Error('вместо файла получена HTML-страница Google Drive');
      return buffer;
    } catch (error) {
      lastError = error;
      log(`Повтор ${attempt}/${retries}: ${label}: ${error.message}`);
      await sleep(900 * attempt + Math.floor(Math.random() * 500));
    }
  }
  throw lastError;
}

async function fetchDriveFile(id, label, retries = 4) {
  const urls = [
    `https://drive.usercontent.google.com/download?id=${encodeURIComponent(id)}&export=download&confirm=t`,
    `https://drive.google.com/uc?export=download&id=${encodeURIComponent(id)}&confirm=t`,
  ];
  let lastError;
  for (const url of urls) {
    try { return await fetchBufferFromUrl(url, label, retries); }
    catch (error) { lastError = error; }
  }
  throw lastError;
}

const driveCache = new Map();
async function cachedDriveFile(id, label) {
  if (!driveCache.has(id)) {
    driveCache.set(id, fetchDriveFile(id, label).then(buffer => {
      log(`Загружено: ${label} — ${buffer.length} байт`);
      return buffer;
    }));
  }
  return driveCache.get(id);
}

async function pool(items, concurrency, worker) {
  const results = new Array(items.length);
  let next = 0;
  async function runner() {
    while (true) {
      const index = next++;
      if (index >= items.length) return;
      try { results[index] = await worker(items[index], index); }
      catch (error) { results[index] = { error }; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length || 1) }, runner));
  return results;
}

function readRegistry(buffer) {
  const workbook = XLSX.read(buffer, { type: 'buffer', cellDates: false, raw: false });
  const rowsBySheet = {};
  for (const album of ALBUMS) {
    const ws = workbook.Sheets[album.sheet];
    if (!ws) throw new Error(`В рабочем реестре отсутствует вкладка «${album.sheet}». Найдены: ${workbook.SheetNames.join(', ')}`);
    const grid = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '', raw: false });
    const rows = [];
    for (const cells of grid.slice(1)) {
      const number = Number.parseInt(String(cells[0] ?? '').trim(), 10);
      if (!Number.isFinite(number)) continue;
      rows.push({
        sheet: album.sheet,
        number,
        excelRow: Number.parseInt(String(cells[1] ?? '').trim(), 10) || 0,
        registryName: String(cells[2] ?? '').trim(),
        docNumber: String(cells[3] ?? '').trim(),
        organization: String(cells[4] ?? '').trim(),
        originalStatus: String(cells[5] ?? '').trim(),
        foundName: String(cells[6] ?? '').trim(),
        folder: String(cells[7] ?? '').trim(),
        driveUrl: String(cells[8] ?? '').trim(),
        originalNote: String(cells[9] ?? '').trim(),
        sources: [],
        placeholder: false,
        placeholderHeading: '',
        finalStatus: '',
        finalNote: '',
        pageStart: 0,
        pageEnd: 0,
        componentNames: [],
      });
    }
    rows.sort((a, b) => a.number - b.number);
    if (rows.length !== album.expected) throw new Error(`Вкладка «${album.sheet}»: ожидалось ${album.expected} позиций, прочитано ${rows.length}`);
    rowsBySheet[album.sheet] = rows;
  }
  return { workbook, rowsBySheet };
}

function wrapText(text, font, size, maxWidth) {
  const paragraphs = String(text ?? '').split(/\r?\n/);
  const lines = [];
  for (const paragraph of paragraphs) {
    const words = paragraph.split(/\s+/).filter(Boolean);
    if (!words.length) { lines.push(''); continue; }
    let line = '';
    for (const word of words) {
      const candidate = line ? `${line} ${word}` : word;
      if (font.widthOfTextAtSize(candidate, size) <= maxWidth) line = candidate;
      else {
        if (line) lines.push(line);
        if (font.widthOfTextAtSize(word, size) <= maxWidth) line = word;
        else {
          let chunk = '';
          for (const char of word) {
            const next = chunk + char;
            if (font.widthOfTextAtSize(next, size) <= maxWidth) chunk = next;
            else { if (chunk) lines.push(chunk); chunk = char; }
          }
          line = chunk;
        }
      }
    }
    if (line) lines.push(line);
  }
  return lines;
}

function drawTextBlock(page, text, { x, y, width, font, size = 9, lineHeight = 12, color = COLOR.black, maxLines = 100 }) {
  const lines = wrapText(text, font, size, width).slice(0, maxLines);
  let currentY = y;
  for (const line of lines) {
    page.drawText(line, { x, y: currentY, font, size, color });
    currentY -= lineHeight;
  }
  return currentY;
}

async function loadFonts() {
  const regularUrl = 'https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf';
  const boldUrl = 'https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-Bold.ttf';
  const [regular, bold] = await Promise.all([
    fetchBufferFromUrl(regularUrl, 'NotoSans-Regular.ttf'),
    fetchBufferFromUrl(boldUrl, 'NotoSans-Bold.ttf'),
  ]);
  return { regular, bold };
}

async function createOnePagePdf(fontBytes, boldBytes, drawer) {
  const doc = await PDFDocument.create();
  doc.registerFontkit(fontkit);
  const font = await doc.embedFont(fontBytes, { subset: true });
  const bold = await doc.embedFont(boldBytes, { subset: true });
  const page = doc.addPage(A4);
  await drawer({ doc, page, font, bold });
  return Buffer.from(await doc.save({ useObjectStreams: true }));
}

async function makePlaceholder(row, fonts) {
  return createOnePagePdf(fonts.regular, fonts.bold, async ({ page, font, bold }) => {
    const [w, h] = A4;
    page.drawRectangle({ x: 38, y: 38, width: w - 76, height: h - 76, borderWidth: 2.2, borderColor: COLOR.red });
    const heading = row.placeholderHeading || 'ЛИСТ / ДОКУМЕНТ НЕ НАЙДЕН';
    const headingLines = wrapText(heading, bold, 15, w - 120);
    let y = h - 78;
    for (const line of headingLines) {
      const tw = bold.widthOfTextAtSize(line, 15);
      page.drawText(line, { x: (w - tw) / 2, y, font: bold, size: 15, color: COLOR.red });
      y -= 21;
    }
    y -= 10;
    const items = [
      ['Альбом / вкладка', row.sheet],
      ['Позиция реестра', String(row.number)],
      ['Строка исходного Excel', String(row.excelRow || '—')],
      ['Требуемое наименование', row.registryName],
      ['Номер / дата', row.docNumber || '—'],
      ['Организация', row.organization || '—'],
      ['Исходный статус', row.originalStatus || '—'],
      ['Ближайший найденный файл', row.foundName || '—'],
      ['Место хранения', row.folder || '—'],
      ['Итог повторной проверки', row.finalStatus],
      ['Пояснение', row.finalNote || '—'],
    ];
    for (const [label, value] of items) {
      if (y < 80) break;
      page.drawText(`${label}:`, { x: 62, y, font: bold, size: 8.6, color: COLOR.navy });
      y -= 12;
      y = drawTextBlock(page, value, { x: 72, y, width: w - 142, font, size: 8.2, lineHeight: 10.4, maxLines: 7 });
      y -= 7;
    }
    page.drawText('Информационный лист ПТО. Фиксирует отсутствие точного документа либо неполное соответствие реестру.', {
      x: 62, y: 52, font, size: 7.2, color: COLOR.gray,
    });
  });
}

async function makeCover(album, rows, fonts) {
  return createOnePagePdf(fonts.regular, fonts.bold, async ({ page, font, bold }) => {
    const [w, h] = A4;
    page.drawRectangle({ x: 0, y: h - 130, width: w, height: 130, color: COLOR.navy });
    const top = 'ИСПОЛНИТЕЛЬНАЯ ДОКУМЕНТАЦИЯ';
    page.drawText(top, { x: (w - bold.widthOfTextAtSize(top, 17)) / 2, y: h - 58, font: bold, size: 17, color: COLOR.white });
    const titleLines = wrapText(album.title, bold, 20, w - 100);
    let y = h - 185;
    for (const line of titleLines) {
      page.drawText(line, { x: (w - bold.widthOfTextAtSize(line, 20)) / 2, y, font: bold, size: 20, color: COLOR.navy });
      y -= 29;
    }
    y -= 15;
    const object = 'Объект: школа на 550 мест, Ново-Переделкино';
    page.drawText(object, { x: (w - font.widthOfTextAtSize(object, 10.5)) / 2, y, font, size: 10.5, color: COLOR.black });
    y -= 40;
    const total = rows.length;
    const exact = rows.filter(r => !r.placeholder).length;
    const placeholders = rows.filter(r => r.placeholder).length;
    const noSource = rows.filter(r => !r.sources.length).length;
    page.drawRectangle({ x: 88, y: y - 116, width: w - 176, height: 138, color: COLOR.lightGray, borderColor: COLOR.blue, borderWidth: 1 });
    page.drawText('Контроль комплектности', { x: 112, y: y - 2, font: bold, size: 12, color: COLOR.navy });
    const stats = [
      `Позиций по реестру: ${total}`,
      `Подшито без заглушки: ${exact}`,
      `Позиций с информационной заглушкой: ${placeholders}`,
      `Позиций без найденного исходного PDF: ${noSource}`,
    ];
    let sy = y - 30;
    for (const stat of stats) { page.drawText(stat, { x: 118, y: sy, font, size: 10, color: COLOR.black }); sy -= 24; }
    page.drawText(`Сформировано: ${new Date().toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })} (МСК)`, { x: 58, y: 48, font, size: 8, color: COLOR.gray });
    page.drawText('Проверочная сборка ПТО', { x: w - 210, y: 48, font: bold, size: 8, color: COLOR.gray });
  });
}

async function makeRegistryPages(album, rows, fonts) {
  const doc = await PDFDocument.create();
  doc.registerFontkit(fontkit);
  const font = await doc.embedFont(fonts.regular, { subset: true });
  const bold = await doc.embedFont(fonts.bold, { subset: true });
  const perPage = 14;
  for (let offset = 0; offset < rows.length; offset += perPage) {
    const page = doc.addPage(A4);
    const [w, h] = A4;
    page.drawText(`РЕЕСТР СОСТАВА — ${album.title.toUpperCase()}`, { x: 44, y: h - 48, font: bold, size: 11.2, color: COLOR.navy });
    let y = h - 78;
    const chunk = rows.slice(offset, offset + perPage);
    for (const row of chunk) {
      const fill = row.placeholder ? COLOR.orange : COLOR.blue;
      page.drawRectangle({ x: 42, y: y - 37, width: w - 84, height: 43, color: fill, opacity: 0.38, borderColor: COLOR.gray, borderWidth: 0.35 });
      page.drawText(String(row.number).padStart(2, '0'), { x: 49, y: y - 11, font: bold, size: 8.5, color: COLOR.navy });
      drawTextBlock(page, row.registryName, { x: 74, y: y - 7, width: 315, font, size: 7.1, lineHeight: 8.5, maxLines: 3 });
      drawTextBlock(page, row.finalStatus, { x: 398, y: y - 7, width: 145, font: row.placeholder ? bold : font, size: 6.5, lineHeight: 8, color: row.placeholder ? COLOR.red : COLOR.black, maxLines: 4 });
      y -= 49;
    }
    page.drawText(`Страница реестра ${Math.floor(offset / perPage) + 1} из ${Math.ceil(rows.length / perPage)}`, { x: 44, y: 28, font, size: 7, color: COLOR.gray });
  }
  return Buffer.from(await doc.save({ useObjectStreams: true }));
}

async function extractPdfPages(buffer, pageNumbers) {
  const src = await PDFDocument.load(buffer, { ignoreEncryption: true, updateMetadata: false });
  const out = await PDFDocument.create();
  const indices = pageNumbers.map(n => n - 1).filter(n => n >= 0 && n < src.getPageCount());
  if (!indices.length) throw new Error(`В PDF нет запрошенных страниц: ${pageNumbers.join(', ')}`);
  const pages = await out.copyPages(src, indices);
  pages.forEach(p => out.addPage(p));
  return Buffer.from(await out.save({ useObjectStreams: true }));
}

async function validatePdf(buffer, label) {
  try {
    const doc = await PDFDocument.load(buffer, { ignoreEncryption: true, updateMetadata: false });
    const count = doc.getPageCount();
    if (!count) throw new Error('PDF не содержит страниц');
    return count;
  } catch (error) {
    throw new Error(`${label}: поврежденный/нечитаемый PDF — ${error.message}`);
  }
}

async function prepareRows(rowsBySheet) {
  const allRows = ALBUMS.flatMap(a => rowsBySheet[a.sheet]);
  const requests = [];
  for (const row of allRows) {
    const ids = extractDriveIds(row.driveUrl);
    const names = splitList(row.foundName);
    for (let i = 0; i < Math.max(ids.length, names.length); i++) {
      if (ids[i]) requests.push({ id: ids[i], label: names[i] || `source_${i + 1}.pdf` });
    }
  }
  const unique = [...new Map(requests.map(r => [r.id, r])).values()];
  log(`Уникальных PDF-ссылок рабочего реестра к загрузке: ${unique.length}`);
  await pool(unique, 6, async request => {
    try { await cachedDriveFile(request.id, request.label); return { ok: true, id: request.id }; }
    catch (error) { log(`ОШИБКА ЗАГРУЗКИ ${request.label}: ${error.message}`); return { ok: false, id: request.id, error: error.message }; }
  });

  let akScan = null;
  try {
    akScan = await cachedDriveFile(SCAN_AK_ID, 'scan_0264.pdf');
    await validatePdf(akScan, 'scan_0264.pdf');
  } catch (error) {
    log(`scan_0264.pdf недоступен: ${error.message}`);
  }

  for (const row of allRows) {
    const ids = extractDriveIds(row.driveUrl);
    const names = splitList(row.foundName);
    const sourceCount = Math.max(ids.length, names.length);
    const errors = [];
    for (let i = 0; i < sourceCount; i++) {
      const id = ids[i] || '';
      const label = names[i] || `source_${i + 1}.pdf`;
      if (!id) { errors.push(`${label}: ссылка отсутствует`); continue; }
      try {
        const buffer = await cachedDriveFile(id, label);
        const pageCount = await validatePdf(buffer, label);
        row.sources.push({ id, label, buffer, pageCount, sha256: hash(buffer), selectedPages: null });
      } catch (error) { errors.push(error.message); }
    }

    const flaggedPartial = normalize(row.originalStatus).includes('проверить');
    const flaggedMissing = normalize(row.originalStatus).includes('нет в архиве');
    const code = `${row.registryName} ${row.docNumber}`.match(/АК-00[12]/i)?.[0]?.toUpperCase();

    if (code && akScan) {
      const pages = code === 'АК-001' ? [1, 2] : [3, 4];
      try {
        const extracted = await extractPdfPages(akScan, pages);
        row.sources = [{ id: SCAN_AK_ID, label: `scan_0264.pdf — предполагаемый ${code}, страницы ${pages.join('–')}`, buffer: extracted, pageCount: pages.length, sha256: hash(extracted), selectedPages: pages }];
        row.placeholder = true;
        row.placeholderHeading = 'ТОЧНЫЙ ПРОТОКОЛ ИЗ РЕЕСТРА НЕ ПОДТВЕРЖДЕН';
        row.finalStatus = 'ЗАГЛУШКА + КАНДИДАТ ИЗ SCAN_0264.PDF';
        row.finalNote = `Отдельный файл ${code} во всех проверенных папках и архивной структуре не найден. После заглушки подшиты страницы ${pages.join('–')} файла scan_0264.pdf как ближайший кандидат; номер и состав необходимо подтвердить ответственному ПТО.`;
        continue;
      } catch (error) { errors.push(`scan_0264.pdf: ${error.message}`); }
    }

    if (!row.sources.length) {
      row.placeholder = true;
      row.placeholderHeading = 'ЛИСТ / ДОКУМЕНТ НЕ НАЙДЕН';
      row.finalStatus = 'НЕ НАЙДЕНО — ВСТАВЛЕНА ЗАГЛУШКА';
      row.finalNote = [row.originalNote, ...errors, 'Повторно проверены папки материалов, все осевые папки «Анохин», извлеченная структура М082-2322-09-НФС изм.1 и соответствующий RAR-архив. Точного PDF по позиции не выявлено.'].filter(Boolean).join(' ');
    } else if (flaggedPartial || flaggedMissing) {
      row.placeholder = true;
      row.placeholderHeading = flaggedMissing ? 'ТОЧНЫЙ ДОКУМЕНТ ИЗ РЕЕСТРА НЕ ПОДТВЕРЖДЕН' : 'ТОЧНОЕ СООТВЕТСТВИЕ РЕЕСТРУ НЕ НАЙДЕНО';
      row.finalStatus = 'ЗАГЛУШКА + БЛИЖАЙШИЙ НАЙДЕННЫЙ ЛИСТ';
      row.finalNote = [row.originalNote, ...errors, 'После заглушки подшит ближайший найденный PDF. Заглушка сохранена, поскольку оси, номер листа либо наличие отдельного листа узлов не совпадают с формулировкой реестра полностью.'].filter(Boolean).join(' ');
    } else {
      row.placeholder = false;
      row.finalStatus = 'НАЙДЕНО И ПОДШИТО';
      row.finalNote = errors.join(' ');
    }
  }
  return allRows;
}

async function appendPdf(albumDoc, buffer, label) {
  const src = await PDFDocument.load(buffer, { ignoreEncryption: true, updateMetadata: false });
  const indices = Array.from({ length: src.getPageCount() }, (_, i) => i);
  const pages = await albumDoc.copyPages(src, indices);
  pages.forEach(p => albumDoc.addPage(p));
  return pages.length;
}

async function createZip(zipPath, entries) {
  await new Promise((resolve, reject) => {
    const output = fs.createWriteStream(zipPath);
    const archive = archiver('zip', { zlib: { level: 6 } });
    output.on('close', resolve);
    output.on('error', reject);
    archive.on('warning', error => error.code === 'ENOENT' ? log(`ZIP warning: ${error.message}`) : reject(error));
    archive.on('error', reject);
    archive.pipe(output);
    for (const entry of entries) archive.append(entry.buffer, { name: entry.name });
    archive.finalize();
  });
}

async function buildAlbum(album, rows, fonts) {
  const doc = await PDFDocument.create();
  doc.setTitle(album.title);
  doc.setSubject('Исполнительная документация по фасадам Ш550');
  doc.setAuthor('ПТО — проверочная сборка');
  doc.setCreator('SH550 Vercel PDF Builder');
  doc.setProducer('pdf-lib');
  const zipEntries = [];

  const cover = await makeCover(album, rows, fonts);
  await appendPdf(doc, cover, 'cover');
  zipEntries.push({ name: '000_Titul.pdf', buffer: cover });
  const registry = await makeRegistryPages(album, rows, fonts);
  await appendPdf(doc, registry, 'registry');
  zipEntries.push({ name: '001_Reestr_sostava.pdf', buffer: registry });

  for (const row of rows) {
    const start = doc.getPageCount() + 1;
    row.componentNames = [];
    if (row.placeholder) {
      const placeholder = await makePlaceholder(row, fonts);
      const name = `${String(row.number).padStart(3, '0')}_00_ZAGLUSHKA.pdf`;
      await appendPdf(doc, placeholder, name);
      zipEntries.push({ name, buffer: placeholder });
      row.componentNames.push(name);
    }
    let sourceIndex = 0;
    for (const source of row.sources) {
      sourceIndex += 1;
      const letter = row.sources.length > 1 ? String.fromCharCode(96 + sourceIndex) : '';
      const name = `${String(row.number).padStart(3, '0')}_${letter}_${safeName(source.label, 96)}${source.label.toLowerCase().endsWith('.pdf') ? '' : '.pdf'}`;
      try {
        await appendPdf(doc, source.buffer, name);
        zipEntries.push({ name, buffer: source.buffer });
        row.componentNames.push(name);
      } catch (error) {
        const failure = { ...row, placeholderHeading: 'ОШИБКА ПОДШИВКИ НАЙДЕННОГО PDF', finalStatus: 'НАЙДЕННЫЙ PDF НЕ ПОДШИТ', finalNote: `${row.finalNote} ${source.label}: ${error.message}`.trim() };
        const placeholder = await makePlaceholder(failure, fonts);
        const errorName = `${String(row.number).padStart(3, '0')}_${letter}_ERROR.pdf`;
        await appendPdf(doc, placeholder, errorName);
        zipEntries.push({ name: errorName, buffer: placeholder });
        row.componentNames.push(errorName);
        row.placeholder = true;
        row.finalStatus = 'ОШИБКА ПОДШИВКИ — ВСТАВЛЕНА ЗАГЛУШКА';
        row.finalNote = failure.finalNote;
      }
    }
    if (doc.getPageCount() < start) {
      const emergency = { ...row, placeholder: true, placeholderHeading: 'ПОЗИЦИЯ НЕ СФОРМИРОВАНА', finalStatus: 'ТЕХНИЧЕСКАЯ ЗАГЛУШКА', finalNote: row.finalNote || 'Нет доступного исходного PDF.' };
      const placeholder = await makePlaceholder(emergency, fonts);
      const name = `${String(row.number).padStart(3, '0')}_EMERGENCY_ZAGLUSHKA.pdf`;
      await appendPdf(doc, placeholder, name);
      zipEntries.push({ name, buffer: placeholder });
      row.componentNames.push(name);
      row.placeholder = true;
      row.finalStatus = emergency.finalStatus;
      row.finalNote = emergency.finalNote;
    }
    row.pageStart = start;
    row.pageEnd = doc.getPageCount();
  }

  const albumBuffer = Buffer.from(await doc.save({ useObjectStreams: true, addDefaultPage: false }));
  await validatePdf(albumBuffer, album.file);
  fs.writeFileSync(path.join(OUTPUT, album.file), albumBuffer);
  await createZip(path.join(OUTPUT, album.zip), zipEntries);
  return {
    title: album.title,
    sheet: album.sheet,
    file: album.file,
    zip: album.zip,
    positions: rows.length,
    pages: doc.getPageCount(),
    placeholders: rows.filter(r => r.placeholder).length,
    withoutSource: rows.filter(r => !r.sources.length).length,
    size: albumBuffer.length,
    sha256: hash(albumBuffer),
  };
}

async function makeQcPdf(stats, allRows, fonts) {
  const doc = await PDFDocument.create();
  doc.registerFontkit(fontkit);
  const font = await doc.embedFont(fonts.regular, { subset: true });
  const bold = await doc.embedFont(fonts.bold, { subset: true });
  let page = doc.addPage(A4);
  let y = A4[1] - 50;
  page.drawText('ОТЧЁТ КОНТРОЛЯ КОМПЛЕКТНОСТИ Ш550 — ФАСАДЫ', { x: 42, y, font: bold, size: 13, color: COLOR.navy });
  y -= 34;
  const summary = [
    `Проверено позиций реестра: ${allRows.length}`,
    `Позиции с информационными заглушками: ${allRows.filter(r => r.placeholder).length}`,
    `Позиции без найденного исходного PDF: ${allRows.filter(r => !r.sources.length).length}`,
    `Проверенные источники: прямые ссылки реестра; папки материалов; все осевые папки «Анохин»; извлеченная структура и RAR М082-2322-09-НФС изм.1; scan_0264.pdf.`,
  ];
  for (const line of summary) { y = drawTextBlock(page, line, { x: 48, y, width: 500, font, size: 9, lineHeight: 12, maxLines: 8 }); y -= 8; }
  y -= 6;
  page.drawText('Итоги по альбомам', { x: 48, y, font: bold, size: 11, color: COLOR.navy }); y -= 22;
  for (const stat of stats) {
    const line = `${stat.title}: ${stat.positions} поз.; ${stat.pages} стр.; заглушек ${stat.placeholders}; без исходного PDF ${stat.withoutSource}.`;
    y = drawTextBlock(page, line, { x: 55, y, width: 490, font, size: 8.5, lineHeight: 11, maxLines: 4 }); y -= 7;
  }
  page = doc.addPage(A4); y = A4[1] - 48;
  page.drawText('ПОЗИЦИИ С ЗАГЛУШКАМИ / ЗАМЕЧАНИЯМИ', { x: 42, y, font: bold, size: 12, color: COLOR.red }); y -= 24;
  for (const row of allRows.filter(r => r.placeholder)) {
    const block = `${row.sheet}, № ${row.number}: ${row.registryName}\n${row.finalStatus}. ${row.finalNote}\nСтраницы альбома: ${row.pageStart}–${row.pageEnd}.`;
    const lines = wrapText(block, font, 7.3, 500);
    const needed = lines.length * 9 + 14;
    if (y - needed < 42) { page = doc.addPage(A4); y = A4[1] - 46; }
    page.drawRectangle({ x: 42, y: y - needed + 3, width: 510, height: needed, color: COLOR.orange, opacity: 0.24, borderColor: COLOR.gray, borderWidth: 0.3 });
    y = drawTextBlock(page, block, { x: 50, y: y - 7, width: 494, font, size: 7.3, lineHeight: 9, maxLines: 20 });
    y -= 9;
  }
  return Buffer.from(await doc.save({ useObjectStreams: true }));
}

function updateWorkbook(workbook, rowsBySheet, albumMap) {
  for (const album of ALBUMS) {
    const ws = workbook.Sheets[album.sheet];
    XLSX.utils.sheet_add_aoa(ws, [['Финальный статус', 'Файл альбома', 'Страницы альбома', 'Компоненты ZIP', 'Контрольное примечание']], { origin: 'K1' });
    const range = XLSX.utils.decode_range(ws['!ref']);
    const numberToExcelRow = new Map();
    for (let r = 1; r <= range.e.r; r++) {
      const value = ws[XLSX.utils.encode_cell({ r, c: 0 })]?.v;
      const number = Number.parseInt(String(value ?? '').trim(), 10);
      if (Number.isFinite(number)) numberToExcelRow.set(number, r);
    }
    for (const row of rowsBySheet[album.sheet]) {
      const r = numberToExcelRow.get(row.number);
      if (r == null) continue;
      const values = [row.finalStatus, album.file, `${row.pageStart}-${row.pageEnd}`, row.componentNames.join('\n'), row.finalNote];
      values.forEach((value, i) => { ws[XLSX.utils.encode_cell({ r, c: 10 + i })] = { t: 's', v: value }; });
    }
    ws['!ref'] = XLSX.utils.encode_range({ s: range.s, e: { r: range.e.r, c: 14 } });
  }
  const summary = [['Альбом', 'Позиций', 'Найдено без заглушки', 'С заглушкой', 'Без исходного PDF', 'Файл PDF']];
  for (const album of ALBUMS) {
    const rows = rowsBySheet[album.sheet];
    summary.push([album.title, rows.length, rows.filter(r => !r.placeholder).length, rows.filter(r => r.placeholder).length, rows.filter(r => !r.sources.length).length, album.file]);
  }
  if (!workbook.SheetNames.includes('Финальная проверка')) workbook.SheetNames.unshift('Финальная проверка');
  workbook.Sheets['Финальная проверка'] = XLSX.utils.aoa_to_sheet(summary);
  return XLSX.write(workbook, { type: 'buffer', bookType: 'xlsx' });
}

function buildIndex(stats, report) {
  const rows = stats.map(s => `<tr><td>${s.title}</td><td>${s.positions}</td><td>${s.pages}</td><td>${s.placeholders}</td><td><a href="/${s.file}">PDF</a></td><td><a href="/${s.zip}">ZIP состава</a></td></tr>`).join('');
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ш550 — альбомы исполнительной документации</title><style>body{font-family:Arial,sans-serif;max-width:1180px;margin:40px auto;padding:0 20px;color:#1f2937}h1{color:#183a5a}table{border-collapse:collapse;width:100%;margin:24px 0}th,td{border:1px solid #cbd5e1;padding:10px;text-align:left;vertical-align:top}th{background:#dbe7f4}a{color:#075ea8;font-weight:700}.alert{background:#fff3e0;border-left:5px solid #d97706;padding:14px}.ok{background:#e9f7ef;border-left:5px solid #1e8449;padding:14px}.links a{display:inline-block;margin:8px 16px 8px 0}</style></head><body><h1>Исполнительная документация Ш550 — фасады</h1><p class="ok">Сформировано 5 PDF-альбомов по принципу «одна вкладка реестра — один PDF». Все позиции размещены в порядке Excel-реестра.</p><p class="alert">Для отсутствующих или неполностью совпадающих позиций в PDF вставлен отдельный информационный лист-заглушка. Когда найден ближайший кандидат, он следует сразу после заглушки.</p><table><thead><tr><th>Альбом</th><th>Позиций</th><th>Страниц</th><th>Заглушек</th><th>Альбом</th><th>Исходники</th></tr></thead><tbody>${rows}</tbody></table><div class="links"><a href="/SH550_Facade_Albums_FINAL.zip">Скачать весь комплект ZIP</a><a href="/SH550_Final_Working_Registry.xlsx">Рабочий Excel-реестр</a><a href="/SH550_QC_report.pdf">Отчёт контроля PDF</a><a href="/SH550_QC_report.json">Отчёт контроля JSON</a></div><p>Всего позиций: ${report.registryPositions}. Заглушек: ${report.placeholders}. Без найденного исходного PDF: ${report.withoutSourcePdf}.</p></body></html>`;
}

async function main() {
  log('Старт повторной сборки Ш550');
  const fonts = await loadFonts();
  log('Шрифты загружены');
  const registryBuffer = await fetchDriveFile(WORKING_REGISTRY_ID, 'рабочий реестр SH550');
  const originalRegistryBuffer = await fetchDriveFile(ORIGINAL_REGISTRY_ID, 'исходный реестр альбомов');
  const { workbook, rowsBySheet } = readRegistry(registryBuffer);
  const allRows = await prepareRows(rowsBySheet);
  const stats = [];
  for (const album of ALBUMS) {
    log(`Сборка: ${album.title}`);
    const stat = await buildAlbum(album, rowsBySheet[album.sheet], fonts);
    stats.push(stat);
    log(`Готово: ${album.file}; страниц ${stat.pages}; заглушек ${stat.placeholders}`);
  }

  const qcPdf = await makeQcPdf(stats, allRows, fonts);
  fs.writeFileSync(path.join(OUTPUT, 'SH550_QC_report.pdf'), qcPdf);
  const updatedXlsx = updateWorkbook(workbook, rowsBySheet, Object.fromEntries(stats.map(s => [s.sheet, s.file])));
  fs.writeFileSync(path.join(OUTPUT, 'SH550_Final_Working_Registry.xlsx'), updatedXlsx);
  fs.writeFileSync(path.join(OUTPUT, 'SH550_Source_Registry_Original.xlsx'), originalRegistryBuffer);

  const qcRows = allRows.map(row => ({
    album: row.sheet, number: row.number, excelRow: row.excelRow, registryName: row.registryName,
    docNumber: row.docNumber, organization: row.organization, originalStatus: row.originalStatus,
    foundName: row.foundName, folder: row.folder, driveUrl: row.driveUrl, finalStatus: row.finalStatus,
    placeholder: row.placeholder, finalNote: row.finalNote, albumPages: `${row.pageStart}-${row.pageEnd}`,
    componentNames: row.componentNames, sources: row.sources.map(s => ({ id: s.id, label: s.label, pages: s.pageCount, sha256: s.sha256 })),
  }));
  const report = {
    generatedAtUtc: new Date().toISOString(),
    project: 'Исполнительная документация Ш550 — фасады',
    registryPositions: allRows.length,
    placeholders: allRows.filter(r => r.placeholder).length,
    withoutSourcePdf: allRows.filter(r => !r.sources.length).length,
    albums: stats,
    archiveSearch: {
      rarArchiveId: RAR_ARCHIVE_ID,
      checked: true,
      method: 'Повторно проверены исходный RAR и его извлеченная структура на Google Drive, все осевые папки Анохин, папки материалов и scan_0264.pdf. В итоговую подшивку включены точные документы из реестра; при неполном совпадении — заглушка и ближайший кандидат.',
      confirmedGaps: [
        'Нет отдельной осевой папки/точного комплекта 2/Т-И и 3-6/И; найден ближайший комплект 3/Т-И.',
        'Нет отдельной осевой папки/точного комплекта 5-6/И-Е и 5-7/И; найден ближайший комплект 5-7/Е-И.',
        'В папке 17-1/Т отсутствуют отдельные PDF узлов для ряда листов утепления, направляющих и керамогранита; основной лист включен после заглушки.',
        'АК-001 и АК-002 отдельными файлами не найдены; страницы scan_0264.pdf включены как кандидаты после заглушек.',
      ],
    },
    rows: qcRows,
  };
  fs.writeFileSync(path.join(OUTPUT, 'SH550_QC_report.json'), JSON.stringify(report, null, 2));
  const csvHeader = ['Альбом','№','Строка Excel','Наименование по реестру','Номер/дата','Организация','Исходный статус','Найденный файл','Папка','Drive URL','Финальный статус','Заглушка','Страницы альбома','Контрольное примечание'];
  const csvLines = [csvHeader.map(csvEscape).join(';')];
  for (const row of allRows) csvLines.push([row.sheet,row.number,row.excelRow,row.registryName,row.docNumber,row.organization,row.originalStatus,row.foundName,row.folder,row.driveUrl,row.finalStatus,row.placeholder ? 'ДА' : 'НЕТ',`${row.pageStart}-${row.pageEnd}`,row.finalNote].map(csvEscape).join(';'));
  fs.writeFileSync(path.join(OUTPUT, 'SH550_QC_registry.csv'), '\uFEFF' + csvLines.join('\r\n'));

  const masterEntries = [];
  for (const stat of stats) {
    masterEntries.push({ name: stat.file, buffer: fs.readFileSync(path.join(OUTPUT, stat.file)) });
    masterEntries.push({ name: stat.zip, buffer: fs.readFileSync(path.join(OUTPUT, stat.zip)) });
  }
  for (const name of ['SH550_QC_report.pdf','SH550_QC_report.json','SH550_QC_registry.csv','SH550_Final_Working_Registry.xlsx','SH550_Source_Registry_Original.xlsx']) masterEntries.push({ name, buffer: fs.readFileSync(path.join(OUTPUT, name)) });
  await createZip(path.join(OUTPUT, 'SH550_Facade_Albums_FINAL.zip'), masterEntries);
  fs.writeFileSync(path.join(OUTPUT, 'index.html'), buildIndex(stats, report));
  fs.writeFileSync(path.join(OUTPUT, 'BUILD_STATUS.txt'), ['SUCCESS',`Generated: ${report.generatedAtUtc}`,`Registry positions: ${report.registryPositions}`,`Placeholders: ${report.placeholders}`,`Without source PDF: ${report.withoutSourcePdf}`,...stats.map(s => `${s.file}: ${s.positions} positions, ${s.pages} pages, ${s.placeholders} placeholders, ${s.withoutSource} without source, ${s.size} bytes, sha256=${s.sha256}`),`SH550_Facade_Albums_FINAL.zip: ${fs.statSync(path.join(OUTPUT,'SH550_Facade_Albums_FINAL.zip')).size} bytes`].join('\n') + '\n');
  fs.writeFileSync(path.join(OUTPUT, 'build-log.txt'), logLines.join('\n') + '\n');
  log('Сборка успешно завершена');
}

main().catch(error => {
  log(`КРИТИЧЕСКАЯ ОШИБКА: ${error.stack || error.message}`);
  fs.writeFileSync(path.join(OUTPUT, 'BUILD_ERROR.txt'), `${error.stack || error.message}\n\n${logLines.join('\n')}\n`);
  fs.writeFileSync(path.join(OUTPUT, 'index.html'), `<!doctype html><meta charset="utf-8"><h1>Ошибка сборки Ш550</h1><pre>${String(error.stack || error.message).replaceAll('&','&amp;').replaceAll('<','&lt;')}</pre>`);
  process.exitCode = 1;
});
