"use strict";

const DATA_URL = "schedule.json";

const statusElement = document.querySelector("#sync-status");
const scheduleBody = document.querySelector("#schedule-body");
const generatedAtElement = document.querySelector("#generated-at");

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  const parts = new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map(({ type, value: part }) => [type, part]));
  return `${values.year}-${values.month}-${values.day}（${values.weekday}）${values.hour}:${values.minute}`;
}

function safeExternalUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value, window.location.href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function makeLink(label, url, title) {
  const link = document.createElement("a");
  link.textContent = label;
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  if (title) link.title = title;
  return link;
}

function appendChapter(cell, course) {
  const chapter = course.chapter || "";
  const previewUrl = safeExternalUrl(course.preview_pdf_url);
  if (previewUrl) {
    cell.append(makeLink(chapter, previewUrl, course.preview_pdf_name || "開啟預習教材 PDF"));
  } else {
    cell.append(document.createTextNode(chapter));
  }

  const answerUrl = safeExternalUrl(course.answer_pdf_url);
  if (answerUrl) {
    cell.append(document.createTextNode(" （"));
    cell.append(makeLink("解", answerUrl, course.answer_pdf_name || "開啟教材解答 PDF"));
    cell.append(document.createTextNode("）"));
  }
}

function appendRoom(cell, course) {
  const roomLabel = course.room_id || "進入教室";
  const classUrl = safeExternalUrl(course.class_url);
  if (classUrl) {
    cell.append(makeLink(roomLabel, classUrl, "進入線上教室"));
  } else {
    cell.textContent = course.room_id || "";
  }
}

function textCell(value) {
  const cell = document.createElement("td");
  cell.textContent = value || "";
  return cell;
}

function renderCourses(courses) {
  const fragment = document.createDocumentFragment();
  if (!courses.length) {
    const row = document.createElement("tr");
    const cell = textCell("目前沒有課程");
    cell.colSpan = 5;
    row.append(cell);
    fragment.append(row);
  }

  for (const course of courses) {
    const row = document.createElement("tr");
    row.dataset.courseId = course.source_id || "";
    row.append(textCell(formatDateTime(course.start_at)));
    row.append(textCell(course.student_name || course.source_id));
    row.append(textCell(course.grade));

    const chapterCell = document.createElement("td");
    appendChapter(chapterCell, course);
    row.append(chapterCell);

    const roomCell = document.createElement("td");
    appendRoom(roomCell, course);
    row.append(roomCell);
    fragment.append(row);
  }

  scheduleBody.replaceChildren(fragment);
}

function renderStatus(state) {
  statusElement.classList.remove("error");
  statusElement.textContent = state
    ? `最後成功同步：${state.last_success_at}｜目前共 ${state.fetched_count} 堂`
    : "尚未同步";
}

async function loadSchedule() {
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderStatus(data.state ?? null);
    renderCourses(Array.isArray(data.courses) ? data.courses : []);
    generatedAtElement.textContent = `報表產生時間：${data.generated_at || ""}`;
  } catch (error) {
    statusElement.classList.add("error");
    statusElement.textContent = "課表載入失敗，請確認 schedule.json 可以透過 HTTP 讀取。";
    scheduleBody.replaceChildren();
    console.error("Unable to load schedule", error);
  }
}

loadSchedule();
