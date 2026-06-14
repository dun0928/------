#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Collect Educoder task requirements and submitted code for report drafting.

Default browser: Microsoft Edge.

Usage:
  python collect_educoder_report.py --url "https://www.educoder.net/..."

The script opens a persistent Edge profile under scripts/.edge-profile.
Log in manually in the opened browser the first time. After login, press Enter
in the terminal and the script will start collecting the current challenge.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR.parent / "educoder_collected_report_data.json"
DEFAULT_SCREENSHOT_DIR = SCRIPT_DIR.parent / "screenshots"
DEFAULT_PROFILE_DIR = SCRIPT_DIR / ".edge-profile"


@dataclass
class ChallengeRecord:
    chapter: str
    index: int
    title: str
    requirement: str
    code: str
    screenshot: str


@dataclass
class ChapterEntry:
    index: int
    chapter_no: int
    title: str
    href: str
    tooltip_id: str


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def short(value: str, limit: int = 80) -> str:
    value = normalize_text(value).replace("\n", " ")
    return value if len(value) <= limit else value[: limit - 3] + "..."


def safe_filename(value: str, fallback: str) -> str:
    value = normalize_text(value)
    value = re.sub(r'[\\/:*?"<>|\s]+', "_", value).strip("._")
    return (value or fallback)[:80]


def parse_chapter_range(value: str) -> tuple[int, int] | None:
    value = normalize_text(value)
    if not value:
        return None
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", value)
    if not match:
        raise ValueError("chapter range must be like 'x-y' or 'x'")
    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    if start < 1 or end < start:
        raise ValueError("chapter range must use 1-based indexes and satisfy start <= end")
    return start, end


def parse_chapter_no(title: str) -> int | None:
    match = re.match(r"^\s*第?\s*(\d+)\s*[.、．]", title)
    return int(match.group(1)) if match else None


def load_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Missing dependency: playwright\n"
            "Install with:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install msedge",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return sync_playwright, PlaywrightTimeoutError


def visible_text(locator) -> str:
    try:
        if locator.count() == 0:
            return ""
        return normalize_text(locator.first.inner_text(timeout=2500))
    except Exception:
        return ""


def first_visible_text(page, selectors: list[str]) -> str:
    for selector in selectors:
        text = visible_text(page.locator(selector))
        if text:
            return text
    return ""


def first_locator(page, selectors: list[str]):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.count() > 0:
                return locator.first
        except Exception:
            continue
    return None


def scroll_locator(locator, steps: int = 16, amount: int = 720) -> None:
    try:
        locator.first.scroll_into_view_if_needed(timeout=1500)
    except Exception:
        return
    for _ in range(steps):
        try:
            locator.first.evaluate(
                "(el, amount) => { el.scrollTop = Math.min(el.scrollTop + amount, el.scrollHeight); }",
                amount,
            )
        except Exception:
            break
        time.sleep(0.08)
    for _ in range(steps):
        try:
            locator.first.evaluate("(el, amount) => { el.scrollTop = Math.max(el.scrollTop - amount, 0); }", amount)
        except Exception:
            break
        time.sleep(0.04)


def scroll_page(page, steps: int = 8, amount: int = 900) -> None:
    for _ in range(steps):
        try:
            page.mouse.wheel(0, amount)
        except Exception:
            return
        time.sleep(0.08)
    for _ in range(steps):
        try:
            page.mouse.wheel(0, -amount)
        except Exception:
            return
        time.sleep(0.04)


def scroll_likely_panels(page) -> None:
    selectors = [
        ".task-left-panel",
        ".challenge-left",
        ".left-panel",
        ".markdown-body",
        ".task-desc",
        ".challenge-desc",
        ".monaco-editor",
        ".view-lines",
        ".CodeMirror-scroll",
        "textarea",
        "main",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.count() > 0:
                scroll_locator(locator)
        except Exception:
            continue
    scroll_page(page)


def extract_code_file(page) -> str:
    js = r"""
    () => {
      const out = [];
      const seen = new Set();

      function add(text) {
        if (!text) return;
        text = String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
        if (!text || seen.has(text)) return;
        seen.add(text);
        out.push(text);
      }

      function isVisible(el) {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
      }

      function scoreEditor(el) {
        const rect = el.getBoundingClientRect();
        let score = rect.left;
        const text = (el.innerText || el.textContent || "").slice(0, 400);
        const boxText = (el.closest("section, main, div")?.innerText || "").slice(0, 1500);
        if (/代码文件|文件|编辑器|code/i.test(boxText)) score += 2000;
        if (/任务描述|实验要求|任务要求|实训要求|参考答案|提示/i.test(boxText)) score -= 1200;
        if (/^\s*(select|create|alter|insert|update|delete|drop|grant|revoke|delimiter|import|public|class|package)\b/i.test(text)) score += 500;
        return score;
      }

      const roots = Array.from(document.querySelectorAll(".monaco-editor, .CodeMirror, textarea"))
        .filter(isVisible)
        .sort((a, b) => scoreEditor(b) - scoreEditor(a));

      const root = roots[0];
      if (!root) return "";

      if (root.matches("textarea")) {
        add(root.value || root.innerText || root.textContent);
        return out.join("\n\n--- code block ---\n\n");
      }

      if (root.matches(".monaco-editor")) {
        const textarea = root.querySelector("textarea");
        if (textarea && textarea.value) add(textarea.value);
        const lines = Array.from(root.querySelectorAll(".view-lines .view-line"))
          .map((line) => line.innerText || line.textContent || "")
          .join("\n");
        add(lines);
        return out.join("\n\n--- code block ---\n\n");
      }

      if (root.matches(".CodeMirror")) {
        add(root.CodeMirror && root.CodeMirror.getValue ? root.CodeMirror.getValue() : root.innerText);
      }

      return out.join("\n\n--- code block ---\n\n");
    }
    """
    try:
        return normalize_text(page.evaluate(js))
    except Exception:
        return ""


def extract_requirement(page) -> str:
    selectors = [
        ".task-left-panel",
        ".challenge-left",
        ".left-panel",
        ".markdown-body",
        ".task-desc",
        ".challenge-desc",
        ".challenge-content",
        ".subject-content",
        "[class*='task'] [class*='desc']",
        "[class*='challenge'] [class*='desc']",
    ]

    container = first_locator(page, selectors)
    if container is not None:
        try:
            text = container.evaluate(
                r"""
                (root) => {
                  function norm(text) {
                    return String(text || "")
                      .replace(/\r\n/g, "\n")
                      .replace(/\r/g, "\n")
                      .replace(/[ \t]+\n/g, "\n")
                      .replace(/\n{3,}/g, "\n\n")
                      .trim();
                  }

                  function tableToMarkdown(table) {
                    const rows = Array.from(table.querySelectorAll("tr")).map((tr) =>
                      Array.from(tr.querySelectorAll("th,td")).map((cell) =>
                        norm(cell.innerText || cell.textContent).replace(/\n/g, " ")
                      )
                    ).filter((row) => row.length > 0);
                    if (!rows.length) return "";
                    const width = Math.max(...rows.map((row) => row.length));
                    const filled = rows.map((row) => row.concat(Array(Math.max(0, width - row.length)).fill("")));
                    const header = filled[0];
                    const sep = Array(width).fill("---");
                    const body = filled.slice(1);
                    return [header, sep, ...body].map((row) => "| " + row.join(" | ") + " |").join("\n");
                  }

                  function blockText(el) {
                    if (el.matches("pre")) {
                      const code = norm(el.innerText || el.textContent);
                      return code ? "```\n" + code + "\n```" : "";
                    }
                    if (el.matches("table")) return tableToMarkdown(el);
                    if (el.matches("ul,ol")) {
                      return Array.from(el.querySelectorAll(":scope > li"))
                        .map((li) => "- " + norm(li.innerText || li.textContent).replace(/\n/g, "\n  "))
                        .filter(Boolean)
                        .join("\n");
                    }
                    const clone = el.cloneNode(true);
                    clone.querySelectorAll("script,style,.monaco-editor,.CodeMirror,textarea").forEach((node) => node.remove());
                    clone.querySelectorAll("code").forEach((code) => {
                      if (code.closest("pre")) return;
                      code.replaceWith("`" + norm(code.innerText || code.textContent) + "`");
                    });
                    return norm(clone.innerText || clone.textContent);
                  }

                  const blocks = [];
                  const seen = new Set();
                  const candidates = Array.from(root.querySelectorAll("h1,h2,h3,h4,h5,p,pre,table,ul,ol,blockquote"));
                  const source = candidates.length ? candidates : [root];

                  for (const el of source) {
                    if (el.closest(".monaco-editor,.CodeMirror,textarea")) continue;
                    if (el.closest("pre") && !el.matches("pre")) continue;
                    if (el.closest("table") && !el.matches("table")) continue;
                    if ((el.closest("ul,ol") && !el.matches("ul,ol")) || el.matches("li")) continue;
                    const text = blockText(el);
                    if (!text || seen.has(text)) continue;
                    seen.add(text);
                    blocks.push(text);
                  }

                  return blocks.join("\n\n");
                }
                """
            )
            text = normalize_text(text)
            if text:
                return text
        except Exception:
            pass

    try:
        body = page.locator("body").inner_text(timeout=3000)
        return normalize_text(body)
    except Exception:
        return ""


def extract_title(page) -> str:
    selectors = [
        "h1",
        "h2",
        ".challenge-title",
        ".task-title",
        "[class*='title']",
    ]
    text = first_visible_text(page, selectors)
    if text:
        return short(text, 120)
    try:
        return page.title()
    except Exception:
        return ""


def page_signature(page) -> str:
    try:
        body = page.locator("body").inner_text(timeout=2000)
    except Exception:
        body = ""
    return f"{page.url}\n{short(body, 1000)}"


def wait_for_page_ready(page, PlaywrightTimeoutError):
    try:
        page.bring_to_front()
    except Exception:
        pass
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    time.sleep(1.0)
    return page


def active_page_after_click(page, known_pages: list[Any], PlaywrightTimeoutError, timeout: float = 5.0):
    context = page.context
    deadline = time.time() + timeout
    selected = page

    while time.time() < deadline:
        pages = [item for item in context.pages if not item.is_closed()]
        new_pages = [item for item in pages if item not in known_pages]
        if new_pages:
            selected = new_pages[-1]
            break
        if page.is_closed() and pages:
            selected = pages[-1]
            break
        time.sleep(0.2)

    return wait_for_page_ready(selected, PlaywrightTimeoutError)


def click_and_select_page(page, click_action, PlaywrightTimeoutError):
    known_pages = [item for item in page.context.pages if not item.is_closed()]
    try:
        with page.context.expect_page(timeout=6000) as new_page_info:
            click_action()
        return wait_for_page_ready(new_page_info.value, PlaywrightTimeoutError)
    except PlaywrightTimeoutError:
        return active_page_after_click(page, known_pages, PlaywrightTimeoutError, timeout=3.0)


def debug_context_pages(page, label: str) -> None:
    try:
        urls = [item.url for item in page.context.pages if not item.is_closed()]
    except Exception as exc:
        print(f"    debug: {label}: cannot list pages: {exc}")
        return
    print(f"    debug: {label}: open pages={len(urls)}")
    for idx, url in enumerate(urls, start=1):
        print(f"    debug:   page[{idx}] {url}")


def find_existing_chapter_page(page, entry: ChapterEntry, PlaywrightTimeoutError):
    for candidate in reversed([item for item in page.context.pages if not item.is_closed()]):
        if candidate == page:
            continue
        url = candidate.url
        if "/shixun_homework/" not in url or "/detail" not in url:
            continue
        wait_for_page_ready(candidate, PlaywrightTimeoutError)
        try:
            body = candidate.locator("body").inner_text(timeout=3000)
        except Exception:
            body = ""
        core = re.sub(r"^第?\s*\d+\s*[.、．]\s*", "", entry.title)
        core = re.sub(r"[（(]\d+\s*分[)）]\s*$", "", core).strip()
        if not core or core[:20] in body or entry.title[:20] in body:
            return candidate
    return None


def is_chapter_detail_page(page) -> bool:
    return "/shixun_homework/" in page.url and "/detail" in page.url


def is_likely_challenge_page(page) -> bool:
    if is_chapter_detail_page(page):
        return False
    try:
        if page.locator(".monaco-editor, .CodeMirror, textarea, .task-left-panel, .challenge-left").count() > 0:
            return True
    except Exception:
        pass
    if not re.search(r"/tasks/|/myshixuns/|/challenges/", page.url):
        return False
    try:
        text = page.locator("body").inner_text(timeout=2500)
    except Exception:
        return False
    return bool(re.search(r"任务描述|编程要求|测试说明|评测|下一关|代码文件", text))


def click_previous_challenge(page) -> bool:
    candidates = [
        page.get_by_text("上一关", exact=True),
        page.get_by_text("上一关", exact=False),
        page.get_by_text("上一步", exact=False),
        page.locator("button:has-text('上一关')"),
        page.locator("a:has-text('上一关')"),
        page.locator("button:has-text('上一步')"),
        page.locator("a:has-text('上一步')"),
        page.locator(".prev:visible"),
        page.locator("[class*='prev']:visible"),
        page.locator("[class*='previous']:visible"),
    ]
    before = page_signature(page)
    for locator in candidates:
        try:
            if locator.count() == 0:
                continue
            locator.first.scroll_into_view_if_needed(timeout=1500)
            locator.first.click(timeout=3000)
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(1.0)
            return page_signature(page) != before
        except Exception:
            continue
    return False


def is_duplicate(records: list[ChallengeRecord], chapter: str, title: str, requirement: str, code: str) -> bool:
    if not records:
        return False
    last = records[-1]
    return last.chapter == chapter and last.title == title and last.requirement == requirement and last.code == code


def find_chapter_entries(page) -> list[ChapterEntry]:
    js = r"""
    () => {
      function norm(text) {
        return String(text || "")
          .replace(/\r\n/g, "\n")
          .replace(/\r/g, "\n")
          .replace(/[ \t]+\n/g, "\n")
          .replace(/\n{2,}/g, "\n")
          .trim();
      }

      function isVisible(el) {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
      }

      function cleanTitle(text) {
        return norm(text)
          .split("\n")
          .map((line) => line.trim())
          .filter((line) => line && !/^(查看实战|开始实战|进入实战|继续实战|开始学习|继续学习|实战)$/.test(line))
          .join(" ");
      }

      function titleFromAncestors(control) {
        let node = control.parentElement;
        for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
          const title = cleanTitle(node.innerText || node.textContent);
          if (title && title.length <= 180 && title !== cleanTitle(control.innerText || control.textContent)) {
            return title;
          }
        }
        const card = control.closest(
          "li,[class*='item'],[class*='card'],[class*='chapter'],[class*='subject'],[class*='shixun']"
        );
        return card ? cleanTitle(card.innerText || card.textContent) : "";
      }

      function hrefOf(el) {
        const link = el.matches("a") ? el : el.querySelector("a") || el.closest("a");
        return link && link.href ? link.href : "";
      }

      function looksLikeChapter(text) {
        return /^第?\s*\d+\s*[.、．]/.test(text) || /^\d+\s*[.、．]\s*\S+/.test(text);
      }

      const rawCandidates = Array.from(document.querySelectorAll("a,li,[role='button'],[class*='listItem'],[class*='item'],[class*='card'],[class*='chapter']"))
        .filter((el) => {
          if (!isVisible(el)) return false;
          const title = cleanTitle(el.innerText || el.textContent);
          if (!title || title.length > 240) return false;
          if (/查看实战|开始实战|进入实战|继续实战|开始学习|继续学习/.test(title)) return false;
          return looksLikeChapter(title);
        });

      const out = [];
      const seen = new Set();

      Array.from(document.querySelectorAll("[role='tooltip'],.ant-tooltip-inner")).forEach((tooltip, index) => {
        const title = cleanTitle(tooltip.innerText || tooltip.textContent);
        if (!title || title.length > 240 || !looksLikeChapter(title)) return;
        const tooltipId = tooltip.id || "";
        const trigger = tooltipId ? document.querySelector(`[aria-describedby="${CSS.escape(tooltipId)}"]`) : null;
        const card = trigger ? trigger.closest("[class*='listItem'],li,[class*='item'],[class*='card'],[class*='homework'],[class*='shixun'],[class*='chapter']") : null;
        const href = trigger ? hrefOf(card || trigger) : "";
        const key = href || tooltipId || title;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({ index, title, href, tooltipId });
      });

      rawCandidates.forEach((candidate, index) => {
        const title = cleanTitle(candidate.innerText || candidate.textContent) || `章节${index + 1}`;
        const href = hrefOf(candidate);
        const key = href || title;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({ index: out.length + index, title, href, tooltipId: "" });
      });

      const bodyLines = norm(document.body.innerText || document.body.textContent)
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line && line.length <= 180 && looksLikeChapter(line));

      bodyLines.forEach((title, offset) => {
        if (seen.has(title)) return;
        seen.add(title);
        out.push({ index: out.length + rawCandidates.length + offset, title, href: "", tooltipId: "" });
      });

      return out;
    }
    """
    try:
        entries = page.evaluate(js)
    except Exception:
        return []

    result: list[ChapterEntry] = []
    for item in entries or []:
        try:
            title = short(str(item.get("title", "") or f"章节{len(result) + 1}"), 160)
            result.append(
                ChapterEntry(
                    index=int(item.get("index", len(result))),
                    chapter_no=parse_chapter_no(title) or len(result) + 1,
                    title=title,
                    href=str(item.get("href", "") or ""),
                    tooltip_id=str(item.get("tooltipId", "") or ""),
                )
            )
        except Exception:
            continue
    if result:
        result.sort(key=lambda entry: (entry.chapter_no, entry.index))
    return result


def click_practice_entry(page, PlaywrightTimeoutError):
    candidates = [
        page.locator("button.ant-btn:has-text('查看实战')"),
        page.locator("button.ant-btn:has-text('开始实战')"),
        page.locator("button.ant-btn:has-text('进入实战')"),
        page.locator("button.ant-btn:has-text('继续实战')"),
        page.locator("button:has-text('查看实战')"),
        page.locator("a:has-text('查看实战')"),
        page.locator("button:has-text('开始实战')"),
        page.locator("a:has-text('开始实战')"),
        page.locator("button:has-text('进入实战')"),
        page.locator("a:has-text('进入实战')"),
        page.locator("button:has-text('继续实战')"),
        page.locator("a:has-text('继续实战')"),
    ]
    for locator in candidates:
        try:
            if locator.count() == 0:
                continue
            target = locator.first
            target.scroll_into_view_if_needed(timeout=1500)
            before = page_signature(page)
            def click_target():
                try:
                    target.click(timeout=4000)
                except Exception:
                    box = target.bounding_box()
                    if box:
                        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

            active_page = click_and_select_page(page, click_target, PlaywrightTimeoutError)
            if is_likely_challenge_page(active_page) or page_signature(active_page) != before:
                return active_page
        except Exception:
            continue
    return page if is_likely_challenge_page(page) else None


def click_target_coordinates(page, x: float, y: float, PlaywrightTimeoutError):
    return click_and_select_page(page, lambda: page.mouse.click(x, y), PlaywrightTimeoutError)


def debug_chapter_cards(page, entry: ChapterEntry) -> None:
    try:
        cards = page.evaluate(
            r"""
            ({ chapterNo, title }) => {
              function norm(text) {
                return String(text || "").replace(/\s+/g, " ").trim();
              }
              function rectOf(el) {
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {
                  x: Math.round(rect.x),
                  y: Math.round(rect.y),
                  width: Math.round(rect.width),
                  height: Math.round(rect.height),
                };
              }
              const prefix = new RegExp(`^\\s*第?\\s*${chapterNo}\\s*[.、．]`);
              return Array.from(document.querySelectorAll("[class*='listItem']"))
                .slice(0, 30)
                .map((card, idx) => {
                  const name = card.querySelector("[class*='name']");
                  const action = card.querySelector("[class*='actionIcon'],[class*='flexBox']");
                  const links = Array.from(card.querySelectorAll("a")).map((a) => a.href).filter(Boolean);
                  const cardText = norm(card.innerText || card.textContent);
                  const nameText = norm(name && (name.innerText || name.textContent));
                  return {
                    idx,
                    match: prefix.test(nameText) || nameText === title || cardText.includes(title),
                    nameText,
                    actionText: norm(action && (action.innerText || action.textContent)),
                    cardRect: rectOf(card),
                    actionRect: rectOf(action),
                    links,
                    textHead: cardText.slice(0, 180),
                  };
                });
            }
            """,
            {"chapterNo": entry.chapter_no, "title": entry.title},
        )
    except Exception as exc:
        print(f"    debug: failed to inspect chapter cards: {exc}")
        return

    print(f"    debug: listItem cards found: {len(cards)}")
    for card in cards:
        marker = "*" if card.get("match") else " "
        print(
            "    debug:"
            f"{marker} card[{card.get('idx')}]"
            f" name={short(card.get('nameText') or '', 90)!r}"
            f" action={short(card.get('actionText') or '', 40)!r}"
            f" cardRect={card.get('cardRect')}"
            f" actionRect={card.get('actionRect')}"
        )
        if card.get("links"):
            print(f"    debug:  links={card.get('links')}")


def click_list_item_chapter(page, entry: ChapterEntry, PlaywrightTimeoutError):
    before = page_signature(page)
    try:
        target = page.evaluate(
            r"""
            ({ chapterNo, title }) => {
              function norm(text) {
                return String(text || "").replace(/\s+/g, " ").trim();
              }
              function centerOf(el) {
                const rect = el.getBoundingClientRect();
                if (!rect.width || !rect.height) return null;
                return {
                  x: rect.left + rect.width / 2,
                  y: rect.top + rect.height / 2,
                  text: norm(el.innerText || el.textContent),
                  className: String(el.className || ""),
                };
              }
              const prefix = new RegExp(`^\\s*第?\\s*${chapterNo}\\s*[.、．]`);
              const cards = Array.from(document.querySelectorAll("[class*='listItem']"));
              for (const card of cards) {
                const name = card.querySelector("[class*='name']");
                const nameText = norm(name && (name.innerText || name.textContent));
                const cardText = norm(card.innerText || card.textContent);
                if (!(prefix.test(nameText) || nameText === title || cardText.includes(title))) continue;

                const action = Array.from(card.querySelectorAll("[class*='actionIcon'],[class*='flexBox'],aside,button,a,div"))
                  .find((el) => /开始学习|继续学习/.test(norm(el.innerText || el.textContent)));
                const target = action || card;
                target.scrollIntoView({ block: "center", inline: "center" });
                return centerOf(target);
              }
              return null;
            }
            """,
            {"chapterNo": entry.chapter_no, "title": entry.title},
        )
    except Exception:
        return False

    if not target:
        return None

    try:
        active_page = click_target_coordinates(page, float(target["x"]), float(target["y"]), PlaywrightTimeoutError)
    except Exception:
        return None

    return active_page if page_signature(active_page) != before or is_likely_challenge_page(active_page) else None


def click_chapter_by_visible_content(page, entry: ChapterEntry, PlaywrightTimeoutError):
    before = page_signature(page)
    known_pages = [item for item in page.context.pages if not item.is_closed()]
    try:
        clicked = page.evaluate(
            r"""
            ({ title, chapterNo }) => {
              function norm(text) {
                return String(text || "")
                  .replace(/\r\n/g, "\n")
                  .replace(/\r/g, "\n")
                  .replace(/\s+/g, " ")
                  .trim();
              }

              function isVisible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
              }

              function isTooltip(el) {
                return Boolean(el.closest(".ant-tooltip,[role='tooltip']"));
              }

              function clickableFrom(el) {
                return el.closest(
                  "[class*='listItem'],a,button,[role='button'],li,tr,[class*='item'],[class*='card'],[class*='homework'],[class*='shixun'],[class*='chapter']"
                ) || el;
              }

              function score(el, text) {
                const rect = el.getBoundingClientRect();
                let value = 0;
                if (new RegExp(`^\\s*第?\\s*${chapterNo}\\s*[.、．]`).test(text)) value += 1000;
                if (/开始学习|继续学习/.test(text)) value += 800;
                if (/MySQL|数据库|SQL|存储|查询|事务|完整性|约束/.test(text)) value += 300;
                value -= Math.min(text.length, 500);
                value -= Math.abs(rect.top);
                return value;
              }

              const titleText = norm(title);
              const core = titleText
                .replace(/^第?\s*\d+\s*[.、．]\s*/, "")
                .replace(/[（(]\d+\s*分[)）]\s*$/, "")
                .slice(0, 28);

              const chapterPrefix = new RegExp(`^\\s*第?\\s*${chapterNo}\\s*[.、．]`);
              const candidates = Array.from(document.querySelectorAll("body *"))
                .filter((el) => {
                  if (!isVisible(el) || isTooltip(el)) return false;
                  const text = norm(el.innerText || el.textContent || el.getAttribute("title") || el.getAttribute("aria-label"));
                  if (!text || text.length > 600) return false;
                  return chapterPrefix.test(text) || (core && text.includes(core));
                })
                .map((el) => {
                  const target = clickableFrom(el);
                  const text = norm(target.innerText || target.textContent || el.innerText || el.textContent);
                  return { el, target, text, score: score(target, text) };
                })
                .sort((a, b) => b.score - a.score);

              for (const item of candidates) {
                const practice = Array.from(item.target.querySelectorAll("a,button,[role='button'],aside,div,span"))
                  .find((el) => /开始学习|继续学习/.test(norm(el.innerText || el.textContent)) && isVisible(el));
                const target = practice || item.target;
                target.scrollIntoView({ block: "center", inline: "center" });
                target.click();
                return true;
              }

              return false;
            }
            """,
            {"title": entry.title, "chapterNo": entry.chapter_no},
        )
    except Exception:
        return False

    if not clicked:
        return None

    active_page = active_page_after_click(page, known_pages, PlaywrightTimeoutError)
    return active_page if page_signature(active_page) != before or is_likely_challenge_page(active_page) else None


def enter_chapter(page, entry: ChapterEntry, PlaywrightTimeoutError, debug: bool = False):
    before = page_signature(page)
    existing_page = find_existing_chapter_page(page, entry, PlaywrightTimeoutError)
    if existing_page:
        if debug:
            print(f"    debug: using existing chapter page: {existing_page.url}")
        return existing_page

    if entry.href:
        try:
            page.goto(entry.href, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass
    else:
        active_page = click_list_item_chapter(page, entry, PlaywrightTimeoutError)
        if active_page:
            return active_page

        if entry.tooltip_id:
            try:
                known_pages = [item for item in page.context.pages if not item.is_closed()]
                clicked = page.evaluate(
                    r"""
                    ({ tooltipId }) => {
                      const trigger = document.querySelector(`[aria-describedby="${CSS.escape(tooltipId)}"]`);
                      if (!trigger) return false;
                      const card = trigger.closest(
                        "[class*='listItem'],li,[class*='item'],[class*='card'],[class*='homework'],[class*='shixun'],[class*='chapter']"
                      );
                      const actions = card
                        ? Array.from(card.querySelectorAll("a,button,[role='button'],aside,div,span"))
                        : [];
                      const action = actions.find((el) =>
                        /开始学习|继续学习/.test(el.innerText || el.textContent || "")
                      );
                      const target = action || card || trigger.closest("a,button,[role='button']") || trigger;
                      target.scrollIntoView({ block: "center", inline: "center" });
                      target.click();
                      return true;
                    }
                    """,
                    {"tooltipId": entry.tooltip_id},
                )
                if clicked:
                    page = active_page_after_click(page, known_pages, PlaywrightTimeoutError)
            except Exception:
                pass

        if page_signature(page) == before and not is_likely_challenge_page(page):
            candidates = [
                page.locator(f"[aria-describedby='{entry.tooltip_id}']") if entry.tooltip_id else page.locator("__never__"),
                page.get_by_text(entry.title, exact=True),
                page.get_by_text(entry.title, exact=False),
                page.locator("a,li,[role='button'],[class*='item'],[class*='card'],[class*='chapter']").filter(has_text=entry.title),
            ]
            for locator in candidates:
                try:
                    if locator.count() == 0:
                        continue
                    target = locator.first
                    target.scroll_into_view_if_needed(timeout=1500)
                    known_pages = [item for item in page.context.pages if not item.is_closed()]
                    target.click(timeout=4000)
                    page = active_page_after_click(page, known_pages, PlaywrightTimeoutError)
                    break
                except Exception:
                    continue

        if page_signature(page) == before and not is_likely_challenge_page(page):
            active_page = click_chapter_by_visible_content(page, entry, PlaywrightTimeoutError)
            if active_page:
                page = active_page

    if page_signature(page) == before and not is_likely_challenge_page(page):
        existing_page = find_existing_chapter_page(page, entry, PlaywrightTimeoutError)
        if existing_page:
            if debug:
                print(f"    debug: using existing chapter page after click attempts: {existing_page.url}")
            return existing_page
        if debug:
            print("    debug: page did not change after chapter click attempts.")
            debug_context_pages(page, "after chapter click attempts")
            debug_chapter_cards(page, entry)
        return None
    return page


def collect_challenges(
    page,
    args: argparse.Namespace,
    records: list[ChallengeRecord],
    output: Path,
    screenshot_dir: Path,
    PlaywrightTimeoutError,
    chapter: str,
    start_index: int,
) -> int:
    idx = start_index
    chapter_record_start = len(records)
    duplicate_count = 0
    while idx <= args.max_challenges:
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass

        scroll_likely_panels(page)

        title = extract_title(page)
        requirement = extract_requirement(page)
        code = extract_code_file(page)

        chapter_name = safe_filename(chapter, "chapter")
        screenshot_path = screenshot_dir / f"{idx:02d}_{chapter_name}_challenge.png"
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
            screenshot_value = str(screenshot_path)
        except Exception:
            screenshot_value = ""

        record = ChallengeRecord(
            index=idx,
            chapter=chapter,
            title=title,
            requirement=requirement,
            code=code,
            screenshot=screenshot_value,
        )

        if not is_likely_challenge_page(page):
            print(f"[{idx}] current page does not look like a challenge page, stop current chapter: {page.url}")
            break

        if not is_duplicate(records, record.chapter, record.title, record.requirement, record.code):
            records.append(record)
            save_records(records, output)
            duplicate_count = 0
            print(f"[{idx}] collected: {short(chapter)} / {short(title) or page.url}")
            print(f"    requirement chars: {len(requirement)}, code chars: {len(code)}")
            if not code:
                print("    warning: code is empty; the right-side code editor selector may need adjustment.")
        else:
            duplicate_count += 1
            print(f"[{idx}] skipped duplicate page: {short(chapter)} / {short(title) or page.url}")
            if duplicate_count >= 2:
                print("Repeated duplicate page detected. Stop current chapter.")
                break

        idx += 1

        if args.once:
            break

        if not click_previous_challenge(page):
            print("No clickable '上一关' control found. Stop current chapter.")
            break

        time.sleep(args.delay)

    chapter_records = records[chapter_record_start:]
    if len(chapter_records) > 1:
        first_index = chapter_records[0].index
        last_index = chapter_records[-1].index
        for record in chapter_records:
            record.index = first_index + last_index - record.index
        chapter_records.reverse()
        records[chapter_record_start:] = chapter_records
        save_records(records, output)
        print(f"Reordered {len(chapter_records)} record(s) for chapter index order.")
    elif len(chapter_records) == 1:
        save_records(records, output)

    return idx


def save_records(records: list[ChallengeRecord], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    data: list[dict[str, Any]] = [asdict(record) for record in records]
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    sync_playwright, PlaywrightTimeoutError = load_playwright()

    output = Path(args.output).resolve()
    screenshot_dir = Path(args.screenshot_dir).resolve()
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    records: list[ChallengeRecord] = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(Path(args.profile_dir).resolve()),
            channel="msedge",
            headless=False,
            viewport={"width": args.width, "height": args.height},
            accept_downloads=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded")

        print("Edge is open. Log in manually if Educoder asks for it.")
        print("After the target chapter list or challenge page is visible, return here and press Enter.")
        input()

        entry_url = page.url
        entry_page = page
        chapter_entries = [] if args.single_chapter else find_chapter_entries(page)
        next_index = 1

        if chapter_entries:
            chapter_range = parse_chapter_range(args.chapter_range)
            if chapter_range is not None:
                start_chapter, end_chapter = chapter_range
                chapter_entries = [
                    entry
                    for entry in chapter_entries
                    if start_chapter <= entry.chapter_no <= end_chapter
                ]
                print(f"Using chapter range: {start_chapter}-{end_chapter}")
            print(f"Found {len(chapter_entries)} chapter(s).")
            for entry in chapter_entries:
                if next_index > args.max_challenges:
                    break
                print(f"Entering chapter {entry.chapter_no}: {short(entry.title)}")
                page = context.new_page() if entry_page.is_closed() else entry_page
                try:
                    page.goto(entry_url, wait_until="domcontentloaded")
                    page.wait_for_load_state("networkidle", timeout=15000)
                except PlaywrightTimeoutError:
                    pass
                if args.debug:
                    debug_chapter_cards(page, entry)
                chapter_page = enter_chapter(page, entry, PlaywrightTimeoutError, debug=args.debug)
                if not chapter_page:
                    print(f"    warning: cannot enter chapter, skipped: {short(entry.title)}")
                    continue
                page = chapter_page
                if is_chapter_detail_page(page) or not is_likely_challenge_page(page):
                    if args.debug:
                        print(f"    debug: chapter detail opened; trying to click practice entry: {page.url}")
                    practice_page = click_practice_entry(page, PlaywrightTimeoutError)
                    if not practice_page:
                        print(f"    warning: cannot enter practice, skipped: {short(entry.title)}")
                        continue
                    page = practice_page
                if not is_likely_challenge_page(page):
                    print(f"    warning: practice entry did not open a challenge page, skipped: {short(entry.title)}")
                    continue
                next_index = collect_challenges(
                    page=page,
                    args=args,
                    records=records,
                    output=output,
                    screenshot_dir=screenshot_dir,
                    PlaywrightTimeoutError=PlaywrightTimeoutError,
                    chapter=entry.title,
                    start_index=next_index,
                )
                if args.once:
                    break
        else:
            if not args.single_chapter and not is_likely_challenge_page(page):
                print("No chapter entries were found on this page, and the page is not a challenge page.")
                print("Check that the chapter list is visible in Edge before pressing Enter.")
                save_records(records, output)
                print(f"Saved {len(records)} record(s) to: {output}")
                context.close()
                return
            chapter = args.chapter or extract_title(page) or "当前章节"
            next_index = collect_challenges(
                page=page,
                args=args,
                records=records,
                output=output,
                screenshot_dir=screenshot_dir,
                PlaywrightTimeoutError=PlaywrightTimeoutError,
                chapter=chapter,
                start_index=next_index,
            )

        save_records(records, output)
        print(f"Saved {len(records)} record(s) to: {output}")
        context.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Educoder challenge content for database lab reports.")
    parser.add_argument("--url", required=True, help="Educoder challenge or classroom URL.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path.")
    parser.add_argument("--screenshot-dir", default=str(DEFAULT_SCREENSHOT_DIR), help="Directory for screenshots.")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR), help="Persistent Edge profile directory.")
    parser.add_argument("--max-challenges", type=int, default=50, help="Maximum challenges to collect.")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay after clicking next challenge.")
    parser.add_argument("--width", type=int, default=1440, help="Browser viewport width.")
    parser.add_argument("--height", type=int, default=950, help="Browser viewport height.")
    parser.add_argument("--once", action="store_true", help="Only collect the current page.")
    parser.add_argument("--single-chapter", action="store_true", help="Treat the URL as a single chapter or challenge page.")
    parser.add_argument("--chapter", default="", help="Chapter name used when --single-chapter is enabled or no chapter list is found.")
    parser.add_argument("--chapter-range", default="", help="Chapter range for multi-chapter pages, like 'x-y' or 'x'. Uses 1-based closed indexes.")
    parser.add_argument("--debug", action="store_true", help="Print detailed chapter card matching and click diagnostics.")
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
