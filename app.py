#!/usr/bin/env python3
"""
AI News Dashboard
Fetches the last 3 days of AI news from multiple sources.

Setup:
    pip install flask requests beautifulsoup4 lxml trafilatura

Run:
    python app.py

Then open: http://localhost:5000
"""

import os
import threading
from flask import Flask, jsonify, render_template_string, request
import requests as req_lib
from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta
import re
from collections import Counter
import string
import json
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

CACHE = {
    'data': None,
    'timestamp': None,
    'ttl_seconds': 600,
}

JOB = {'running': False}

STOP_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'this', 'that', 'these',
    'those', 'it', 'its', 'they', 'them', 'their', 'we', 'our', 'you',
    'your', 'he', 'she', 'his', 'her', 'as', 'not', 'no', 'also', 'just',
    'more', 'out', 'up', 'if', 'into', 'which', 'how', 'when', 'what',
    'who', 'all', 'one', 'two', 'three', 'i', 'us', 'am', 'than', 'then',
    'so', 'after', 'before', 'over', 'under', 'while', 'since', 'through',
    'between', 'during', 'without', 'within', 'down', 'off', 'above', 'near',
    'any', 'each', 'both', 'other', 'some', 'said', 'about', 'new', 'use',
    'using', 'used', 'get', 'make', 'like', 'time', 'year', 'day', 'way',
    'first', 'next', 'even', 'now', 'back', 'well', 'only', 'much', 'very',
    'still', 'here', 'most', 'help', 'work', 'set', 'such', 'take',
    'where', 'need', 'say', 'know', 'look', 'give', 'include', 'including',
    'across', 'around', 'come', 'came', 'per', 'been', 'those', 'there',
    'their', 'these', 'than', 'that', 'with', 'from', 'have', 'will',
}

# Known AI companies for company detection in "Various" sources
KNOWN_COMPANIES = sorted([
    'Google DeepMind', 'Google', 'OpenAI', 'Microsoft', 'Meta', 'Apple',
    'Amazon', 'NVIDIA', 'Anthropic', 'DeepMind', 'xAI', 'Mistral AI',
    'Mistral', 'Hugging Face', 'Cohere', 'Stability AI', 'Midjourney',
    'Runway', 'ElevenLabs', 'Perplexity', 'Replit', 'GitHub', 'Tesla',
    'Samsung', 'IBM', 'Oracle', 'Salesforce', 'Adobe', 'Palantir',
    'Scale AI', 'Databricks', 'Snowflake', 'DeepSeek', 'Baidu', 'Alibaba',
    'Tencent', 'ByteDance', 'Inflection', 'Character.AI', 'Character AI',
    'LangChain', 'Pinecone', 'Weaviate', 'AWS', 'Azure', 'TSMC', 'Intel',
    'AMD', 'Qualcomm', 'Arm', 'Cursor', 'Windsurf', 'Bolt', 'Lovable',
    'Vercel', 'Notion', 'Suno', 'Descript', 'n8n', 'Jasper', 'Asana',
    'Atlassian', 'Datadog', 'Elastic', 'Pipedream', 'Bardeen', 'Gumroad',
    'Flowith', 'Gamma', 'Assisterr', 'Higgsfield', 'Wispr', 'Moonshot',
    'Qwen', 'Artlist', 'Capacity', 'Haystack', 'deepset', 'Skew',
    'ChatGPT', 'Gemini', 'Llama', 'Grok', 'Claude',
    'Canva', 'Figma', 'Zapier', 'Helicone', 'Atlas AI', 'Pinecone',
    'Runway', 'Replit', 'Bloomberg', 'Reuters', 'TechCrunch', 'Verge',
], key=len, reverse=True)  # longest match first

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
}

SOURCES = [
    # ── AI Labs / Frontier Models ─────────────────────────────────────────
    {"url": "https://openai.com/pt-BR/news/",                                    "company": "OpenAI",            "website": "OpenAI News"},
    {"url": "https://www.anthropic.com/news",                                    "company": "Anthropic",         "website": "Anthropic News"},
    {"url": "https://mistral.ai/news",                                           "company": "Mistral AI",        "website": "Mistral AI News"},
    {"url": "https://x.ai/news",                                                 "company": "xAI",               "website": "xAI News"},
    {"url": "https://www.moonshot.ai/",                                          "company": "Moonshot AI",       "website": "Moonshot AI"},
    {"url": "https://qwen.ai/news",                                              "company": "Alibaba / Qwen",    "website": "Qwen News"},
    # ── Big Tech ──────────────────────────────────────────────────────────
    {"url": "https://blog.google/innovation-and-ai/",                            "company": "Google",            "website": "Google AI Blog",             "rss_url": "https://blog.google/rss/"},
    {"url": "https://antigravity.google/blog",                                   "company": "Google",            "website": "Google Antigravity Blog"},
    {"url": "https://microsoft.ai/news/",                                        "company": "Microsoft",         "website": "Microsoft AI News"},
    {"url": "https://www.microsoft.com/en-us/microsoft-copilot/blog/tag/news/",  "company": "Microsoft",         "website": "Microsoft Copilot Blog"},
    {"url": "https://www.microsoft.com/en-us/research/project/autogen/news-and-awards/", "company": "Microsoft Research", "website": "AutoGen News"},
    {"url": "https://ai.meta.com/blog/",                                         "company": "Meta",              "website": "AI at Meta Blog"},
    {"url": "https://www.alibabagroup.com/en-US/news-and-resource",              "company": "Alibaba Group",     "website": "Alibaba News"},
    {"url": "https://nvidianews.nvidia.com/",                                    "company": "NVIDIA",            "website": "NVIDIA Newsroom"},
    {"url": "https://ir.tesla.com/press",                                        "company": "Tesla",             "website": "Tesla Press"},
    # ── AI Tools / Assistants ─────────────────────────────────────────────
    {"url": "https://www.perplexity.ai/hub",                                     "company": "Perplexity",        "website": "Perplexity Hub"},
    {"url": "https://suno.com/blog",                                             "company": "Suno",              "website": "Suno Blog"},
    {"url": "https://runwayml.com/news",                                         "company": "Runway",            "website": "Runway AI News"},
    # ── Dev Tools / Coding ────────────────────────────────────────────────
    {"url": "https://cursor.com/blog",                                           "company": "Cursor",            "website": "Cursor Blog"},
    {"url": "https://windsurf.com/blog",                                         "company": "Windsurf",          "website": "Windsurf Blog"},
    {"url": "https://replit.com/news",                                           "company": "Replit",            "website": "Replit News"},
    {"url": "https://lovable.dev/blog?category=announcements",                   "company": "Lovable",           "website": "Lovable Blog"},
    {"url": "https://bolt.new/blog",                                             "company": "Bolt",              "website": "Bolt Blog"},
    {"url": "https://github.blog/ai-and-ml/github-copilot/",                     "company": "GitHub",            "website": "GitHub Blog - Copilot",      "rss_url": "https://github.blog/feed/"},
    {"url": "https://vercel.com/blog/category/v0",                               "company": "Vercel",            "website": "v0 Blog"},
    {"url": "https://www.helicone.ai/blog",                                      "company": "Helicone",          "website": "Helicone Blog"},
    # ── Productivity / Business Tools ─────────────────────────────────────
    {"url": "https://www.notion.com/releases",                                   "company": "Notion",            "website": "Notion Releases"},
    {"url": "https://www.figma.com/newsroom/",                                   "company": "Figma",             "website": "Figma Newsroom"},
    {"url": "https://asana.com/press/releases",                                  "company": "Asana",             "website": "Asana Press"},
    {"url": "https://zapier.com/blog/categories/company-news/",                  "company": "Zapier",            "website": "Zapier Blog"},
    {"url": "https://www.canva.com/newsroom/news/",                              "company": "Canva",             "website": "Canva Newsroom"},
    {"url": "https://www.atlasai.co/blog",                                       "company": "Atlas AI",          "website": "Atlas AI Blog"},
    {"url": "https://gamma.app/docs/News-AI-gbhs88u05m29s0p?mode=doc",           "company": "Gamma",             "website": "Gamma News"},
    # ── AI Infrastructure / Data ──────────────────────────────────────────
    {"url": "https://www.datadoghq.com/blog/ai/",                                "company": "Datadog",           "website": "Datadog AI Blog"},
    {"url": "https://blog.langchain.com/",                                       "company": "LangChain",         "website": "LangChain Blog",             "rss_url": "https://blog.langchain.com/rss/"},
    {"url": "https://www.pinecone.io/newsroom/",                                 "company": "Pinecone",          "website": "Pinecone Newsroom"},
    {"url": "https://pr.tsmc.com/english/latest-news?field_category_target_id=All", "company": "TSMC",           "website": "TSMC News"},
    {"url": "https://blog.n8n.io/tag/news/",                                     "company": "n8n",               "website": "n8n Blog"},
    {"url": "https://pipedream.com/docs/changelog",                              "company": "Pipedream",         "website": "Pipedream Changelog"},
    {"url": "https://www.bardeen.ai/release-notes",                              "company": "Bardeen",           "website": "Bardeen Release Notes"},
    {"url": "https://flowith.io/blog/#news",                                     "company": "Flowith",           "website": "Flowith Blog"},
    {"url": "https://blog.assisterr.ai/latest-news/",                            "company": "Assisterr AI",      "website": "Assisterr AI Blog"},
    {"url": "https://base44.com/changelog",                                      "company": "Base44",            "website": "Base44 Changelog"},
    {"url": "https://www.jasper.ai/press",                                       "company": "Jasper",            "website": "Jasper Press"},
    # ── Creative / Media AI ───────────────────────────────────────────────
    {"url": "https://artlist.io/blog/",                                          "company": "Artlist",           "website": "Artlist Blog"},
    {"url": "https://higgsfield.ai/blog",                                        "company": "Higgsfield",        "website": "Higgsfield Blog"},
    {"url": "https://www.descript.com/blog/category/product-updates",            "company": "Descript",          "website": "Descript Blog"},
    {"url": "https://gumroad.com/blog",                                          "company": "Gumroad",           "website": "Gumroad Blog"},
    {"url": "https://wisprflow.ai/blog",                                         "company": "Wispr Flow",        "website": "Wispr Flow Blog"},
    # ── News / Media ──────────────────────────────────────────────────────
    {"url": "https://www.reuters.com/technology/deepseek/",                       "company": "Various",           "website": "Reuters DeepSeek"},
    {"url": "https://www.reuters.com/technology/artificial-intelligence/",        "company": "Various",           "website": "Reuters AI"},
    {"url": "https://www.artificialintelligence-news.com/",                       "company": "Various",           "website": "AI News"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/",           "company": "Various",           "website": "TechCrunch AI",             "rss_url": "https://techcrunch.com/feed/"},
    {"url": "https://www.wsj.com/tech/ai",                                        "company": "Various",           "website": "WSJ AI"},
    {"url": "https://www.wsj.com/tech?mod=breadcrumb",                            "company": "Various",           "website": "WSJ Technology"},
    {"url": "https://edition.cnn.com/business/tech",                              "company": "Various",           "website": "CNN Business Tech"},
    {"url": "https://www.theverge.com/tech",                                      "company": "Various",           "website": "The Verge Tech",            "rss_url": "https://www.theverge.com/rss/index.xml"},
    {"url": "https://www.bloomberg.com/ai",                                       "company": "Various",           "website": "Bloomberg AI"},
    {"url": "https://news.mit.edu/topic/artificial-intelligence2",                "company": "Various",           "website": "MIT News AI",               "rss_url": "https://news.mit.edu/rss/topic/artificial-intelligence"},
    {"url": "https://spectrum.ieee.org/topic/artificial-intelligence/",           "company": "Various",           "website": "IEEE Spectrum AI"},
    {"url": "https://www.latest.com/category/ai",                                 "company": "Various",           "website": "Latest AI"},
    {"url": "https://www.haystack.tv/tag/ai",                                     "company": "Various",           "website": "Haystack AI"},
    {"url": "https://capacityglobal.com/news/",                                   "company": "Capacity",          "website": "Capacity News"},
    {"url": "https://skew-ai.com/",                                               "company": "Various",           "website": "Skew AI"},
    {"url": "https://futuretools.io/news",                                        "company": "Various",           "website": "Future Tools AI"},
]


# ─────────────────────── Date utilities ──────────────────────────────────────

def parse_date_to_iso(text):
    """Convert various date strings to YYYY-MM-DD. Returns '' on failure."""
    if not text:
        return ''
    t = str(text).strip()
    today = date.today()

    # Already ISO
    m = re.match(r'^(\d{4}-\d{2}-\d{2})', t)
    if m:
        return m.group(1)

    # Relative
    tl = t.lower()
    if any(x in tl for x in ['today', 'just now', 'moments ago']):
        return today.isoformat()
    m = re.search(r'(\d+)\s*(minute|min|second|sec)s?\s*ago', tl)
    if m:
        return today.isoformat()
    m = re.search(r'(\d+)\s*hours?\s*ago', tl)
    if m:
        return today.isoformat()
    m = re.search(r'(\d+)\s*days?\s*ago', tl)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()

    # Strip timezone suffixes for parsing
    clean = re.sub(r'T.*$', '', t).strip()
    clean = re.sub(r'\s+', ' ', clean)

    for fmt in ('%B %d, %Y', '%b %d, %Y', '%B %d %Y', '%b %d %Y',
                '%d %B %Y', '%d %b %Y', '%m/%d/%Y', '%d/%m/%Y',
                '%Y/%m/%d', '%B %Y', '%b %Y'):
        try:
            return datetime.strptime(clean, fmt).date().isoformat()
        except ValueError:
            pass
    return ''


def is_recent(text, days=3):
    """Return True if the date text is within the last `days` days."""
    if not text:
        return False
    tl = str(text).lower().strip()
    today = date.today()
    cutoff = today - timedelta(days=days - 1)

    # Relative expressions always qualify (within today)
    if any(x in tl for x in ['today', 'just now', 'moments ago', 'an hour ago']):
        return True
    if re.search(r'\d+\s*(minute|min|second|sec)s?\s*ago', tl):
        return True
    if re.search(r'\d+\s*hours?\s*ago', tl):
        return True
    m = re.search(r'(\d+)\s*days?\s*ago', tl)
    if m and int(m.group(1)) <= days:
        return True

    iso = parse_date_to_iso(text)
    if iso:
        return cutoff.isoformat() <= iso <= today.isoformat()
    return False


def url_has_recent(url, days=3):
    """Return True if the URL path encodes a date within the last days."""
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=i)
        patterns = [
            f'/{d.year}/{d.month:02d}/{d.day:02d}',
            f'/{d.year}/{d.month}/{d.day}',
            f'/{d.isoformat()}',
            f'/{d.strftime("%Y%m%d")}',
            f'/{d.month:02d}/{d.day:02d}/{d.year}',
        ]
        if any(p in url for p in patterns):
            return True
    return False


# ─────────────────────── Text utilities ──────────────────────────────────────

def clean_text(text):
    """Remove non-printable / binary / mojibake characters from scraped text."""
    if not text:
        return ''
    # Reject binary data (JPEG, PNG, PDF, etc.)
    binary_signatures = ['JFIF', '\x89PNG', '%PDF', 'GIF8', '\xff\xd8']
    for sig in binary_signatures:
        if sig in text[:200]:
            return ''
    # Keep ASCII printable chars + common Western accented letters (Latin-1)
    text = re.sub(r'[^\x20-\x7E\xC0-\xFF\n]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def summarize(text, max_words=120):
    if not text:
        return ''
    text = clean_text(text)
    if not text:
        return ''
    words = text.split()
    # Require at least 50% real English-looking words, else it's garbage
    real_words = [w for w in words if w.isalpha() and len(w) > 1]
    if len(real_words) < 5 or len(real_words) / max(len(words), 1) < 0.4:
        return ''
    result = ' '.join(words[:max_words])
    if len(words) > max_words:
        result += '...'
    return result


def extract_keywords(text, n=5):
    if not text:
        return []
    translator = str.maketrans('', '', string.punctuation)
    words = text.lower().translate(translator).split()
    words = [w for w in words if w not in STOP_WORDS and len(w) > 3 and w.isalpha()]
    if not words:
        return []
    return [w for w, _ in Counter(words).most_common(n)]


def extract_primary_company(title, text=''):
    """Try to identify the primary company from article title / body."""
    combined = (title or '') + ' ' + (text or '')[:800]
    for company in KNOWN_COMPANIES:
        if re.search(r'\b' + re.escape(company) + r'\b', combined, re.I):
            return company
    return ''


# ─────────────────────── HTTP / metadata helpers ─────────────────────────────

def fetch_page(url, timeout=20):
    """Fetch URL and return (BeautifulSoup, final_url)."""
    try:
        r = req_lib.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return BeautifulSoup(r.text, 'lxml'), r.url
    except Exception as e:
        logger.debug(f"fetch_page {url}: {e}")
        return None, url


def extract_meta_date(soup):
    """Extract publication date from article page meta tags / JSON-LD."""
    # Meta tag attributes to check
    for attr, val in [
        ('property', 'article:published_time'),
        ('property', 'article:modified_time'),
        ('property', 'og:updated_time'),
        ('name', 'pubdate'),
        ('name', 'publish-date'),
        ('name', 'publishdate'),
        ('name', 'date'),
        ('name', 'DC.date.issued'),
        ('name', 'DC.Date'),
        ('itemprop', 'datePublished'),
        ('itemprop', 'dateCreated'),
    ]:
        el = soup.find('meta', attrs={attr: val})
        if el and el.get('content'):
            return el['content']

    # JSON-LD
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            items = data if isinstance(data, list) else data.get('@graph', [data])
            for item in items:
                if isinstance(item, dict):
                    d = (item.get('datePublished') or item.get('dateCreated')
                         or item.get('dateModified'))
                    if d:
                        return d
        except Exception:
            pass

    # <time> elements — prefer datetime attr, then text
    for time_el in soup.find_all('time'):
        val = time_el.get('datetime', '')
        if val:
            return val
        txt = time_el.get_text(strip=True)
        if txt and len(txt) > 4:
            return txt

    # itemprop datePublished on any element
    for el in soup.find_all(attrs={'itemprop': re.compile(r'datePublish|dateCreat', re.I)}):
        v = el.get('content', '') or el.get('datetime', '') or el.get_text(strip=True)
        if v:
            return v

    return ''


def extract_meta_image(soup):
    """Extract thumbnail image URL from page meta tags / JSON-LD."""
    for attr, val in [
        ('property', 'og:image'),
        ('name', 'twitter:image'),
        ('property', 'og:image:url'),
        ('name', 'twitter:image:src'),
    ]:
        el = soup.find('meta', attrs={attr: val})
        if el and el.get('content') and el['content'].startswith('http'):
            return el['content']

    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            items = data if isinstance(data, list) else data.get('@graph', [data])
            for item in items:
                if not isinstance(item, dict):
                    continue
                img = item.get('image')
                if isinstance(img, str) and img.startswith('http'):
                    return img
                if isinstance(img, dict):
                    u = img.get('url', '')
                    if u.startswith('http'):
                        return u
                if isinstance(img, list) and img:
                    first = img[0]
                    if isinstance(first, str) and first.startswith('http'):
                        return first
                    if isinstance(first, dict):
                        u = first.get('url', '')
                        if u.startswith('http'):
                            return u
        except Exception:
            pass
    return ''


def fetch_article(url):
    """Fetch article page; return dict with text, date_iso, image_url."""
    result = {'text': '', 'date_iso': '', 'image_url': ''}
    try:
        r = req_lib.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        r.raise_for_status()
        html = r.text
        soup = BeautifulSoup(html, 'lxml')

        raw_date = extract_meta_date(soup)
        result['date_iso'] = parse_date_to_iso(raw_date)
        result['image_url'] = extract_meta_image(soup)

        # Text extraction
        if HAS_TRAFILATURA:
            text = trafilatura.extract(
                html, include_comments=False, include_tables=False, no_fallback=False
            )
            if text and len(text.split()) > 30:
                result['text'] = text
                return result

        # BeautifulSoup fallback
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            tag.decompose()
        for sel in ['article', 'main', '[class*="post-content"]',
                    '[class*="article-body"]', '[class*="content"]', 'body']:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(separator=' ', strip=True)
                if len(t.split()) > 30:
                    result['text'] = t
                    break
    except Exception as e:
        logger.debug(f"fetch_article {url}: {e}")
    return result


# ─────────────────────── RSS feed parser ─────────────────────────────────────

def fetch_rss(rss_url, days=3):
    """Fetch an RSS/Atom feed and return recent article metas."""
    results = []
    try:
        r = req_lib.get(rss_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        # Try XML parser; fall back to html.parser for malformed feeds
        try:
            soup = BeautifulSoup(r.content, 'xml')
        except Exception:
            soup = BeautifulSoup(r.content, 'html.parser')

        items = soup.find_all('item') or soup.find_all('entry')
        for item in items[:30]:
            title_el = item.find('title')
            # RSS uses <link>, Atom uses <link href="..."> or text content
            link_el = item.find('link')
            link = ''
            if link_el:
                link = link_el.get('href', '') or link_el.get_text(strip=True)
            if not link:
                guid = item.find('guid')
                if guid:
                    link = guid.get_text(strip=True)

            date_el = (item.find('pubDate') or item.find('published')
                       or item.find('updated') or item.find('dc:date'))
            date_text = date_el.get_text(strip=True) if date_el else ''

            if not is_recent(date_text, days):
                continue

            title = title_el.get_text(strip=True) if title_el else ''
            if not title or not link:
                continue

            # Image: enclosure or media:content or media:thumbnail
            img_url = ''
            enc = item.find('enclosure')
            if enc and (enc.get('type', '').startswith('image') or enc.get('url', '')):
                img_url = enc.get('url', '')
            if not img_url:
                media = item.find('media:content') or item.find('media:thumbnail')
                if media:
                    img_url = media.get('url', '')

            # Description/teaser — avoids fetching the full article page
            teaser = ''
            for tag in ['description', 'summary', 'content']:
                desc_el = item.find(tag)
                if desc_el:
                    raw = re.sub(r'<[^>]+>', ' ', desc_el.get_text(strip=True))
                    teaser = ' '.join(raw.split()[:120])
                    if teaser:
                        break

            results.append({'title': title, 'url': link,
                            'date_text': date_text, 'image_url': img_url,
                            'teaser': teaser})

    except Exception as e:
        logger.debug(f"fetch_rss {rss_url}: {e}")
    return results


# ─────────────────────── Next.js data extractor ───────────────────────────────

def _walk_json_for_articles(obj, out, depth=0):
    """Recursively search a JSON tree for article-like dicts."""
    if depth > 9 or len(out) > 30:
        return
    if isinstance(obj, dict):
        has_title = any(k in obj for k in ('title', 'headline', 'name', 'heading'))
        has_ref   = any(k in obj for k in ('publishedAt', 'date', 'createdAt',
                                            'publish_date', 'datePublished',
                                            'slug', 'url', 'href', 'link'))
        if has_title and has_ref:
            out.append(obj)
        for v in obj.values():
            _walk_json_for_articles(v, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json_for_articles(item, out, depth + 1)


def extract_nextjs_articles(soup, base_url, days=3):
    """Extract articles from __NEXT_DATA__ (Next.js SSR) embedded in the page."""
    script = soup.find('script', id='__NEXT_DATA__')
    if not script or not script.string:
        return []
    try:
        data = json.loads(script.string)
    except Exception:
        return []

    candidates = []
    _walk_json_for_articles(data, candidates)

    results, seen = [], set()
    for item in candidates:
        title = (item.get('title') or item.get('headline') or
                 item.get('name') or item.get('heading', ''))
        link  = (item.get('url') or item.get('href') or item.get('link') or
                 item.get('path', ''))
        slug  = item.get('slug', '')
        date  = (item.get('publishedAt') or item.get('date') or
                 item.get('createdAt') or item.get('publish_date') or
                 item.get('datePublished', ''))
        img   = item.get('image') or item.get('thumbnail') or item.get('featuredImage', '')
        if isinstance(img, dict):
            img = img.get('url', '') or img.get('src', '')

        if not title:
            continue
        if date and not is_recent(str(date), days):
            continue
        if not link and slug:
            link = urljoin(base_url, slug)
        if link and not link.startswith('http'):
            link = urljoin(base_url, link)
        if not link:
            continue
        img_str = str(img) if img and str(img).startswith('http') else ''
        if link not in seen:
            seen.add(link)
            results.append({'title': str(title), 'url': link,
                            'date_text': str(date) if date else '',
                            'image_url': img_str})
    return results[:5]


# ─────────────────────── Blog-post link fallback ──────────────────────────────

def find_article_links_fallback(soup, base_url, max_per_source=5, days=3):
    """
    When date-based strategies find nothing: collect slug-like links from the
    page and verify their dates by fetching each article individually.
    """
    domain = urlparse(base_url).netloc
    skip = ('/category/', '/tag/', '/topics/', '/author/', '/page/', '/feed',
            '/rss', '/about', '/contact', '/privacy', '/terms', '/search',
            '?s=', '#', '/cdn-cgi/', '/wp-login', '/wp-admin')

    candidates, seen = [], set()
    for a in soup.find_all('a', href=True):
        if len(candidates) >= max_per_source * 4:
            break
        href = a['href']
        if not href.startswith('http'):
            href = urljoin(base_url, href)

        ldom = urlparse(href).netloc
        if domain not in ldom and ldom not in domain:
            continue

        path = urlparse(href).path
        if not path or path == '/':
            continue
        if any(p in href for p in skip):
            continue

        parts = [p for p in path.strip('/').split('/') if p]
        if not parts:
            continue
        last = parts[-1]
        # Must look like a slug: contains hyphens, reasonable length
        if '-' not in last or len(last) < 8:
            continue

        title = a.get_text(strip=True)
        if not title or len(title) < 15:
            continue

        if href not in seen:
            seen.add(href)
            candidates.append({'url': href, 'title': title})

    if not candidates:
        return []

    confirmed = []
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fmap = {pool.submit(fetch_article, c['url']): c
                    for c in candidates[:max_per_source * 3]}
            for future in as_completed(fmap, timeout=50):
                c = fmap[future]
                try:
                    art = future.result(timeout=20)
                    d_iso = art.get('date_iso', '')
                    # Accept if date is recent, OR date is empty (undated blog pages)
                    if is_recent(d_iso, days) or not d_iso:
                        c['_article'] = art
                        confirmed.append(c)
                except Exception:
                    pass
                if len(confirmed) >= max_per_source:
                    break
    except Exception:
        pass

    results = []
    for c in confirmed[:max_per_source]:
        art = c.get('_article', {})
        results.append({'title': c['title'], 'url': c['url'],
                        'date_text': art.get('date_iso', ''),
                        'image_url': art.get('image_url', ''),
                        '_article': art})
    return results


# ─────────────────────── Listing page parser ─────────────────────────────────

def get_date_text_from_element(el):
    for attr in ['datetime', 'data-date', 'data-published', 'content']:
        v = el.get(attr, '')
        if v:
            return v
    time_el = el.find('time')
    if time_el:
        return time_el.get('datetime', '') or time_el.get_text()
    date_el = el.find(class_=re.compile(r'date|time|publish|posted|when|ago', re.I))
    if date_el:
        return date_el.get_text()
    return ''


def find_recent_articles_in_soup(soup, base_url, max_per_source=5, days=3):
    """Return list of {title, url, date_text, image_url} for articles in last days."""
    results, seen = [], set()

    # ── Next.js __NEXT_DATA__ (React/Next.js sites) ───────────────────────
    nextjs = extract_nextjs_articles(soup, base_url, days)
    for item in nextjs:
        if item['url'] not in seen:
            seen.add(item['url'])
            results.append(item)
    if len(results) >= max_per_source:
        return results[:max_per_source]

    # ── JSON-LD ──────────────────────────────────────────────────────────
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            items = data if isinstance(data, list) else data.get('@graph', [data])
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get('@type') not in (
                    'NewsArticle', 'Article', 'BlogPosting', 'WebPage', 'TechArticle'
                ):
                    continue
                date_pub = item.get('datePublished', '') or item.get('dateModified', '')
                if not is_recent(date_pub, days):
                    continue
                art_url = (item.get('url', '')
                           or item.get('mainEntityOfPage', {}).get('@id', ''))
                title = item.get('headline', '') or item.get('name', '')
                img = item.get('image', {})
                img_url = (img.get('url', '') if isinstance(img, dict)
                           else img if isinstance(img, str) else '')
                if art_url and art_url not in seen and title:
                    seen.add(art_url)
                    results.append({'title': title, 'url': art_url,
                                    'date_text': date_pub, 'image_url': img_url})
        except Exception:
            pass

    if len(results) >= max_per_source:
        return results[:max_per_source]

    # ── Article-card containers ───────────────────────────────────────────
    containers = soup.find_all(
        ['article', 'div', 'li', 'section'],
        class_=re.compile(r'post|article|news|story|entry|item|card|feed|blog', re.I),
    )
    for container in containers:
        if len(results) >= max_per_source:
            break
        date_text = get_date_text_from_element(container)
        if not date_text:
            time_el = container.find('time')
            if time_el:
                date_text = time_el.get('datetime', '') or time_el.get_text()
        if not is_recent(date_text, days):
            continue
        link = container.find('a', href=True)
        title_el = container.find(re.compile(r'^h[1-6]$'))
        img_el = container.find('img')
        img_url = ''
        if img_el:
            img_url = (img_el.get('src', '') or img_el.get('data-src', '')
                       or img_el.get('data-lazy-src', ''))
            if img_url and not img_url.startswith('http'):
                img_url = urljoin(base_url, img_url)
        if not link or not title_el:
            continue
        href = link['href']
        if not href.startswith('http'):
            href = urljoin(base_url, href)
        title = title_el.get_text(strip=True)
        # Extract teaser/excerpt from the card to avoid fetching the full article
        teaser = ''
        teaser_el = (container.find(class_=re.compile(
                         r'excerpt|description|summary|teaser|intro|dek|standfirst|lede', re.I))
                     or container.find('p'))
        if teaser_el:
            t = teaser_el.get_text(strip=True)
            if len(t.split()) > 5:
                teaser = ' '.join(t.split()[:120])
        if href not in seen and title:
            seen.add(href)
            results.append({'title': title, 'url': href,
                            'date_text': date_text, 'image_url': img_url,
                            'teaser': teaser})

    if len(results) >= max_per_source:
        return results[:max_per_source]

    # ── Standalone <time> elements ────────────────────────────────────────
    for time_el in soup.find_all('time'):
        if len(results) >= max_per_source:
            break
        date_text = time_el.get('datetime', '') or time_el.get_text()
        if not is_recent(date_text, days):
            continue
        parent = time_el.parent
        for _ in range(6):
            if parent is None:
                break
            link = parent.find('a', href=True)
            heading = parent.find(re.compile(r'^h[1-6]$'))
            if link and heading:
                href = link['href']
                if not href.startswith('http'):
                    href = urljoin(base_url, href)
                title = heading.get_text(strip=True)
                if href not in seen and title:
                    seen.add(href)
                    results.append({'title': title, 'url': href,
                                    'date_text': date_text, 'image_url': ''})
                break
            parent = parent.parent

    if len(results) >= max_per_source:
        return results[:max_per_source]

    # ── URL date pattern ──────────────────────────────────────────────────
    for a in soup.find_all('a', href=True):
        if len(results) >= max_per_source:
            break
        href = a['href']
        if not href.startswith('http'):
            href = urljoin(base_url, href)
        if url_has_recent(href, days):
            title = a.get_text(strip=True)
            if href not in seen and title and len(title) > 10:
                seen.add(href)
                results.append({'title': title, 'url': href,
                                'date_text': '', 'image_url': ''})

    return results[:max_per_source]


# ─────────────────────── Source processor ────────────────────────────────────

def resolve_share_url(share_url):
    try:
        r = req_lib.get(share_url, headers=HEADERS, timeout=15, allow_redirects=True)
        u = r.url
        return u if 'google.com' not in u else None
    except Exception:
        return None


def process_source(source, days=3):
    company_cfg = source.get('company', 'Various')
    website = source.get('website', 'Unknown')
    page_url = source.get('url')
    share_url = source.get('share_url', '')

    if not page_url and share_url:
        resolved = resolve_share_url(share_url)
        if resolved:
            page_url = resolved
            domain = urlparse(resolved).netloc.replace('www.', '')
            if company_cfg in ('Various', 'Unknown'):
                company_cfg = domain.split('.')[0].title()
            if website in ('Unknown', 'Unknown Blog', 'Company News',
                           'Conversational AI', 'Conversational AI Platform'):
                website = domain

    if not page_url:
        return []

    # ── Try RSS feed first (most reliable for dates & content) ───────────
    articles_meta = []
    rss_url = source.get('rss_url', '')
    if rss_url:
        articles_meta = fetch_rss(rss_url, days)

    # ── Fall back to HTML scraping ────────────────────────────────────────
    soup, final_url = None, page_url
    if not articles_meta:
        soup, final_url = fetch_page(page_url)
        if soup:
            articles_meta = find_recent_articles_in_soup(soup, final_url,
                                                         max_per_source=5, days=days)

    # ── Last resort: scan all slug-like links on the page ─────────────────
    if not articles_meta and soup:
        articles_meta = find_article_links_fallback(soup, final_url,
                                                    max_per_source=5, days=days)

    results = []
    for meta in articles_meta:
        art_url = meta['url']
        art_title = meta['title']
        if not art_url or not art_title:
            continue

        # Priority: pre-fetched data > listing teaser > full article fetch
        # Using teaser avoids an extra HTTP request per article (much faster)
        if meta.get('_article'):
            article = meta['_article']
        elif meta.get('teaser'):
            article = {
                'text': meta['teaser'],
                'date_iso': parse_date_to_iso(meta.get('date_text', '')),
                'image_url': meta.get('image_url', ''),
            }
        else:
            article = fetch_article(art_url)
        text = article['text'] or art_title

        # Date: prefer article page metadata, fall back to listing date_text
        date_iso = article['date_iso']
        if not date_iso:
            date_iso = parse_date_to_iso(meta.get('date_text', ''))

        # Skip articles with a confirmed date outside the window
        if date_iso and not is_recent(date_iso, days):
            continue

        # Image: prefer article page og:image, fall back to listing thumbnail
        image_url = article['image_url'] or meta.get('image_url', '')

        summary = summarize(text, 120)
        keywords = extract_keywords(text, 5)

        # Resolve "Various" company to the actual company mentioned
        company = company_cfg
        if company in ('Various', 'Unknown', ''):
            detected = extract_primary_company(art_title, text)
            if detected:
                company = detected
            else:
                company = website  # last resort: use the website name

        results.append({
            'company': company,
            'website': website,
            'link': art_url,
            'title': art_title,
            'summary': summary,
            'keywords': keywords,
            'date_iso': date_iso,
            'image_url': image_url,
        })

    return results


# ─────────────────────── HTML template ───────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>3C TRIX AI | Intelligence Dashboard</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet">
<script>
tailwind.config = {
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "primary": "#001e0c",
        "on-primary": "#ffffff",
        "primary-container": "#0e341d",
        "primary-fixed": "#c3edca",
        "primary-fixed-dim": "#a7d0af",
        "on-primary-fixed": "#00210e",
        "on-primary-fixed-variant": "#294e35",
        "secondary": "#516355",
        "secondary-container": "#d3e8d6",
        "on-secondary-container": "#56695b",
        "secondary-fixed": "#d3e8d6",
        "on-secondary-fixed-variant": "#394b3e",
        "tertiary-fixed": "#d9e6da",
        "on-tertiary-fixed-variant": "#3e4a41",
        "surface": "#faf9f6",
        "surface-container-lowest": "#ffffff",
        "surface-container-low": "#f4f4f0",
        "surface-container": "#eeeeea",
        "surface-container-high": "#e8e8e5",
        "on-surface": "#1a1c1a",
        "on-surface-variant": "#424842",
        "outline": "#727971",
        "outline-variant": "#c1c8c0",
        "background": "#faf9f6",
      },
      fontFamily: {
        "headline": ["Manrope", "sans-serif"],
        "body": ["Inter", "sans-serif"],
      },
    },
  },
}
</script>
<style>
.material-symbols-outlined { font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24; }
.tab-content { display: none; }
.tab-content.active { display: block; }
@keyframes spin { to { transform: rotate(360deg); } }
.spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #c1c8c0; border-top-color: #001e0c; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 6px; }
.kw-tag { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }
.thumb { width: 40px; height: 40px; object-fit: cover; border-radius: 8px; }
.nav-item { transition: all 0.15s; }
.sort-dim { opacity: 0.4; }
.line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
</head>
<body class="bg-surface font-body text-on-surface">

<!-- SIDEBAR (desktop) -->
<aside class="hidden lg:flex flex-col h-screen w-64 fixed left-0 top-0 z-40 bg-surface-container-low py-6 px-4">
  <div class="px-2 mb-8">
    <div class="flex items-center gap-3">
      <div class="w-10 h-10 rounded-xl bg-primary flex items-center justify-center">
        <span class="material-symbols-outlined text-white">psychology</span>
      </div>
      <div>
        <h2 class="text-base font-black font-headline text-primary leading-tight">3C TRIX AI</h2>
        <p class="text-xs text-on-surface-variant opacity-70 font-medium">Intelligence Dashboard</p>
      </div>
    </div>
  </div>
  <nav class="flex flex-col gap-1 flex-grow">
    <button onclick="switchTab('latest')" id="nav-latest" class="nav-item flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-headline font-semibold text-left w-full bg-primary text-white shadow-md">
      <span class="material-symbols-outlined text-xl">newspaper</span> Latest News
    </button>
    <button onclick="switchTab('trending')" id="nav-trending" class="nav-item flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-headline font-semibold text-left w-full text-on-surface-variant hover:bg-surface-container">
      <span class="material-symbols-outlined text-xl">trending_up</span> Trending
    </button>
    <button onclick="switchTab('insights')" id="nav-insights" class="nav-item flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-headline font-semibold text-left w-full text-on-surface-variant hover:bg-surface-container">
      <span class="material-symbols-outlined text-xl">auto_awesome</span> AI Insights
    </button>
  </nav>
  <div class="mt-auto px-2 pt-4 border-t border-outline-variant/20">
    <a href="#" class="flex items-center gap-3 px-4 py-2 text-on-surface-variant hover:text-primary text-sm font-semibold font-headline">
      <span class="material-symbols-outlined text-lg">help_outline</span> Help
    </a>
  </div>
</aside>

<!-- TOP HEADER -->
<header class="fixed top-0 left-0 lg:left-64 right-0 h-16 z-50 bg-surface/90 backdrop-blur-xl border-b border-outline-variant/20">
  <div class="flex items-center justify-between h-full px-6">
    <div class="flex items-center gap-3 lg:hidden">
      <div class="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
        <span class="material-symbols-outlined text-white text-sm">psychology</span>
      </div>
      <span class="font-headline font-black text-primary text-lg">3C TRIX AI</span>
    </div>
    <div class="hidden lg:flex items-center gap-2">
      <span id="header-tab-label" class="text-xl font-headline font-extrabold text-primary">Latest News</span>
    </div>
    <div class="flex items-center gap-3">
      <div id="status" class="text-xs text-on-surface-variant font-medium"></div>
    </div>
  </div>
</header>

<!-- MAIN CONTENT -->
<main class="lg:ml-64 pt-16 min-h-screen">

<!-- ══════════════════ TAB: LATEST NEWS ══════════════════ -->
<div id="tab-latest" class="tab-content active px-6 md:px-8 py-8">
  <div class="mb-8 flex flex-col md:flex-row md:items-end justify-between gap-4">
    <div>
      <h1 class="text-3xl font-black font-headline tracking-tighter text-primary">Latest News</h1>
      <p class="text-on-surface-variant mt-1 font-medium text-sm" id="today-label"></p>
    </div>
    <div class="flex items-center bg-secondary-container/50 px-4 py-2 rounded-xl border border-secondary-container">
      <span class="material-symbols-outlined text-on-secondary-container mr-2 text-sm" style="font-variation-settings:'FILL' 1">analytics</span>
      <span class="text-sm font-bold text-on-secondary-container" id="count-label">Loading\u2026</span>
    </div>
  </div>

  <!-- Control bar -->
  <div class="bg-surface-container-lowest rounded-2xl p-4 mb-6 flex flex-wrap items-center gap-3 shadow-sm border border-outline-variant/10">
    <button id="refresh-btn" onclick="fetchNews(true)"
      class="flex items-center gap-2 bg-primary text-white px-5 py-2.5 rounded-xl font-bold text-sm hover:opacity-90 active:scale-95 transition-all shadow-md font-headline">
      <span class="material-symbols-outlined text-sm">refresh</span> Refresh
    </button>
    <div class="h-8 w-px bg-outline-variant/30 hidden md:block"></div>
    <div class="flex items-center gap-2">
      <label class="text-xs font-bold text-on-surface-variant uppercase tracking-wider">Period</label>
      <div class="relative">
        <select id="f-days" onchange="applyFilters()"
          class="appearance-none bg-surface-container-low border-none rounded-lg py-2 pl-4 pr-8 text-sm font-semibold text-primary focus:ring-2 focus:ring-primary cursor-pointer">
          <option value="1">Last 24h</option>
          <option value="2">Last 2 days</option>
          <option value="3" selected>Last 3 days</option>
        </select>
        <span class="material-symbols-outlined absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-on-surface-variant text-sm">expand_more</span>
      </div>
    </div>
    <div class="flex items-center gap-2">
      <label class="text-xs font-bold text-on-surface-variant uppercase tracking-wider">Website</label>
      <div class="relative">
        <select id="f-website" onchange="applyFilters()"
          class="appearance-none bg-surface-container-low border-none rounded-lg py-2 pl-4 pr-8 text-sm font-semibold text-primary focus:ring-2 focus:ring-primary cursor-pointer">
          <option value="">All websites</option>
        </select>
        <span class="material-symbols-outlined absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-on-surface-variant text-sm">expand_more</span>
      </div>
    </div>
    <div class="flex-grow min-w-[180px] relative">
      <span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant text-lg">filter_list</span>
      <input id="f-keyword" oninput="applyFilters()" type="text" placeholder="Filter by keyword\u2026"
        class="w-full bg-surface-container-low border-none rounded-lg py-2 pl-10 pr-4 text-sm focus:ring-2 focus:ring-primary">
    </div>
  </div>

  <!-- Table -->
  <div class="bg-surface-container-lowest rounded-2xl overflow-hidden shadow-sm border border-outline-variant/10">
    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse">
        <thead>
          <tr class="bg-surface-container text-on-surface-variant text-xs font-bold uppercase tracking-widest">
            <th class="py-4 px-5 cursor-pointer hover:text-primary whitespace-nowrap" onclick="sortBy('date_iso')">
              Date <span id="sort-date_iso" class="sort-dim">\u2195</span>
            </th>
            <th class="py-4 px-5 cursor-pointer hover:text-primary" onclick="sortBy('company')">
              Company <span id="sort-company" class="sort-dim">\u2195</span>
            </th>
            <th class="py-4 px-5 cursor-pointer hover:text-primary whitespace-nowrap" onclick="sortBy('website')">
              Website <span id="sort-website" class="sort-dim">\u2195</span>
            </th>
            <th class="py-4 px-5 text-center">Source</th>
            <th class="py-4 px-5" style="width:32%">Title &amp; Summary</th>
            <th class="py-4 px-5">Keywords</th>
            <th class="py-4 px-5 text-right">Likes &amp; Image</th>
          </tr>
        </thead>
        <tbody id="tbody" class="divide-y divide-surface-container">
          <tr><td colspan="7" class="py-12 text-center text-on-surface-variant">
            <span class="spinner"></span>Loading\u2026
          </td></tr>
        </tbody>
      </table>
    </div>
    <div class="bg-surface-container p-4 flex items-center justify-between">
      <span class="text-xs font-medium text-on-surface-variant" id="table-footer">\u2014</span>
    </div>
  </div>
</div>

<!-- ══════════════════ TAB: TRENDING ══════════════════ -->
<div id="tab-trending" class="tab-content px-6 md:px-8 py-8">
  <header class="mb-10">
    <span class="inline-block px-3 py-1 bg-secondary-container text-on-secondary-container text-xs font-bold rounded-full mb-4 uppercase tracking-widest">Global Trends</span>
    <h1 class="text-4xl md:text-5xl font-headline font-extrabold text-on-surface tracking-tighter leading-none">Trending Intelligence.</h1>
    <p class="mt-3 text-on-surface-variant max-w-xl text-base leading-relaxed">The most-liked AI news from the last 3 days. Like articles in Latest News to surface them here.</p>
  </header>
  <section id="trending-hero" class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-12">
    <div class="lg:col-span-3 text-center py-12 text-on-surface-variant">
      <span class="spinner"></span>Loading trending articles\u2026
    </div>
  </section>
  <section>
    <h2 class="text-xl font-headline font-extrabold tracking-tight mb-6">High Engagement Feed</h2>
    <div id="trending-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5"></div>
  </section>
</div>

<!-- ══════════════════ TAB: AI INSIGHTS ══════════════════ -->
<div id="tab-insights" class="tab-content px-6 md:px-8 py-8">
  <header class="mb-12">
    <div class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-secondary-container text-on-secondary-container text-xs font-bold tracking-widest uppercase mb-4">
      <span class="material-symbols-outlined text-sm" style="font-variation-settings:'FILL' 1">auto_awesome</span>
      Synthesized Analysis
    </div>
    <h1 class="text-4xl md:text-5xl font-headline font-extrabold tracking-tighter text-on-surface leading-none">AI Intelligence Briefs.</h1>
    <p class="mt-3 text-on-surface-variant max-w-xl text-base leading-relaxed">Three synthesized analyses of what's happening across the AI landscape right now, generated from the latest scraped news.</p>
  </header>
  <div id="insights-container" class="space-y-8">
    <div class="text-center py-16 text-on-surface-variant">
      <span class="spinner"></span>Generating insights\u2026
    </div>
  </div>
</div>

</main>

<!-- MOBILE BOTTOM NAV -->
<nav class="lg:hidden fixed bottom-0 left-0 right-0 bg-surface/95 backdrop-blur-xl border-t border-outline-variant/20 h-16 flex items-center justify-around z-50">
  <button onclick="switchTab('latest')" id="mob-latest" class="flex flex-col items-center gap-0.5 text-xs font-bold text-primary">
    <span class="material-symbols-outlined" style="font-variation-settings:'FILL' 1">newspaper</span> News
  </button>
  <button onclick="switchTab('trending')" id="mob-trending" class="flex flex-col items-center gap-0.5 text-xs font-bold text-on-surface-variant">
    <span class="material-symbols-outlined">trending_up</span> Trending
  </button>
  <button onclick="switchTab('insights')" id="mob-insights" class="flex flex-col items-center gap-0.5 text-xs font-bold text-on-surface-variant">
    <span class="material-symbols-outlined">auto_awesome</span> AI Trix
  </button>
</nav>

<script>
// \u2500\u2500 State \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
let allData = [];
let currentTab = 'latest';
let sortState = {col: 'date_iso', dir: 'desc'};
let filters = {website: '', keyword: '', days: 3};
let insightsLoaded = false;
let pollTimer = null;

// \u2500\u2500 Utilities \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function fmtDate(iso) {
  if (!iso) return '\u2014';
  const d = new Date(iso + 'T12:00:00');
  return d.toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'});
}
function fmtLikes(n) {
  if (n >= 1000) return (n/1000).toFixed(1).replace('.0','') + 'k';
  return n ? String(n) : '0';
}

// \u2500\u2500 Likes \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
function getLikes(url) {
  try { return JSON.parse(localStorage.getItem('ai_likes')||'{}')[url] || 0; }
  catch { return 0; }
}
function isLiked(url) {
  try { return !!JSON.parse(localStorage.getItem('ai_liked')||'{}')[url]; }
  catch { return false; }
}
function toggleLike(url, btn) {
  try {
    const likes = JSON.parse(localStorage.getItem('ai_likes')||'{}');
    const liked = JSON.parse(localStorage.getItem('ai_liked')||'{}');
    if (liked[url]) { delete liked[url]; likes[url] = Math.max(0,(likes[url]||1)-1); }
    else { liked[url]=true; likes[url]=(likes[url]||0)+1; }
    localStorage.setItem('ai_likes', JSON.stringify(likes));
    localStorage.setItem('ai_liked', JSON.stringify(liked));
    if (btn) {
      btn.querySelector('.like-icon').style.fontVariationSettings = liked[url] ? "'FILL' 1" : "'FILL' 0";
      btn.querySelector('.like-count').textContent = fmtLikes(likes[url]||0);
      btn.classList.toggle('text-red-500', !!liked[url]);
      btn.classList.toggle('text-on-surface-variant', !liked[url]);
    }
    if (currentTab === 'trending') renderTrending();
  } catch(e) {}
}

// \u2500\u2500 Keyword colors \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
const KW_PAL = [
  ['#c3edca','#294e35'],['#d3e8d6','#394b3e'],['#d9e6da','#3e4a41'],
  ['#e8d5c4','#5a3a1a'],['#d5dce8','#1a2d5a'],['#e8d5e8','#5a1a5a'],
  ['#e8e8c4','#5a5a1a'],['#c4e8e8','#1a5a5a'],['#e8c4c4','#5a1a1a'],
];
function kwColor(w) {
  let h=0; for (let c of w) h=(h*31+c.charCodeAt(0))&0xffff;
  return KW_PAL[h % KW_PAL.length];
}

// \u2500\u2500 Tab switching \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  $('tab-'+tab).classList.add('active');
  ['latest','trending','insights'].forEach(t => {
    const n=$('nav-'+t);
    if (!n) return;
    if (t===tab) { n.className=n.className.replace(/text-on-surface-variant|hover:bg-surface-container/g,'').trim()+' bg-primary text-white shadow-md'; }
    else { n.className=n.className.replace(/bg-primary|text-white|shadow-md/g,'').trim()+' text-on-surface-variant hover:bg-surface-container'; }
  });
  ['latest','trending','insights'].forEach(t => {
    const m=$('mob-'+t);
    if (!m) return;
    m.classList.toggle('text-primary', t===tab);
    m.classList.toggle('text-on-surface-variant', t!==tab);
  });
  const labels={latest:'Latest News',trending:'Trending',insights:'AI Insights'};
  $('header-tab-label').textContent = labels[tab]||'';
  if (tab==='trending') renderTrending();
  if (tab==='insights' && !insightsLoaded) fetchInsights();
}

// \u2500\u2500 Sort icons \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
function updateSortIcons() {
  ['date_iso','company','website'].forEach(col => {
    const el = $('sort-'+col);
    if (!el) return;
    el.className = sortState.col===col ? '' : 'sort-dim';
    el.textContent = sortState.col===col ? (sortState.dir==='asc'?'\u2191':'\u2193') : '\u2195';
  });
}

// \u2500\u2500 Table render \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
function render() {
  const kw = filters.keyword.toLowerCase();
  const ws = filters.website;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - (filters.days - 1));
  const cutISO = cutoff.toISOString().slice(0,10);

  let arr = allData.filter(a => {
    if (ws && a.website !== ws) return false;
    if (kw && !((a.title||'').toLowerCase().includes(kw) ||
                (a.summary||'').toLowerCase().includes(kw) ||
                (a.keywords||[]).some(k=>k.toLowerCase().includes(kw)))) return false;
    if (a.date_iso && a.date_iso < cutISO) return false;
    return true;
  });

  arr.sort((a,b) => {
    const va=a[sortState.col]||'', vb=b[sortState.col]||'';
    const c = va<vb?-1:va>vb?1:0;
    return sortState.dir==='asc'?c:-c;
  });

  $('count-label').textContent = allData.length + ' total articles';
  $('table-footer').textContent = arr.length + ' article' + (arr.length!==1?'s':'') + ' shown';

  if (!arr.length) {
    $('tbody').innerHTML = '<tr><td colspan="7" class="py-12 text-center text-on-surface-variant">No articles match your filters.</td></tr>';
    return;
  }

  $('tbody').innerHTML = arr.map(a => {
    const kws = (a.keywords||[]).map(k => {
      const [bg,fg]=kwColor(k);
      return `<span class="kw-tag" style="background:${bg};color:${fg}">${esc(k)}</span>`;
    }).join(' ');
    const img = a.image_url
      ? `<img class="thumb" src="${esc(a.image_url)}" loading="lazy" onerror="this.style.display='none'" alt="">`
      : '<div class="w-10 h-10 rounded-lg bg-surface-container-high flex-shrink-0"></div>';
    const liked = isLiked(a.link);
    const likes = getLikes(a.link);
    const lid = 'lb'+btoa(encodeURIComponent(a.link||'')).replace(/[^a-z0-9]/gi,'').slice(0,10);
    return `<tr class="hover:bg-surface-container-low transition-colors group">
      <td class="py-5 px-5 text-sm font-semibold whitespace-nowrap">${fmtDate(a.date_iso)}</td>
      <td class="py-5 px-5">
        <div class="flex items-center gap-2">
          <div class="w-7 h-7 rounded bg-primary-fixed flex items-center justify-center text-xs font-bold text-on-primary-fixed-variant flex-shrink-0">${esc((a.company||'?')[0].toUpperCase())}</div>
          <span class="text-sm font-bold text-primary">${esc(a.company)}</span>
        </div>
      </td>
      <td class="py-5 px-5 text-sm text-on-surface-variant">${esc(a.website)}</td>
      <td class="py-5 px-5 text-center">
        <a href="${esc(a.link)}" target="_blank" rel="noopener"
          class="inline-flex items-center justify-center p-2 rounded-lg bg-secondary-container text-on-secondary-container hover:bg-primary hover:text-white transition-all">
          <span class="material-symbols-outlined text-base">link</span>
        </a>
      </td>
      <td class="py-5 px-5">
        <div class="text-sm font-extrabold text-primary mb-1 font-headline leading-snug">${esc(a.title)}</div>
        ${a.summary ? `<div class="text-xs text-on-surface-variant leading-relaxed line-clamp-2">${esc(a.summary)}</div>` : ''}
      </td>
      <td class="py-5 px-5"><div class="flex flex-wrap gap-1">${kws}</div></td>
      <td class="py-5 px-5">
        <div class="flex items-center justify-end gap-3">
          <button id="${lid}" onclick="toggleLike('${esc(a.link)}',this)"
            class="flex items-center gap-1 text-xs font-bold transition-colors ${liked?'text-red-500':'text-on-surface-variant'} hover:text-red-500">
            <span class="material-symbols-outlined like-icon text-sm" style="font-variation-settings:${liked?"'FILL' 1":"'FILL' 0"}">favorite</span>
            <span class="like-count">${fmtLikes(likes)}</span>
          </button>
          ${img}
        </div>
      </td>
    </tr>`;
  }).join('');
}

function sortBy(col) {
  if (sortState.col===col) sortState.dir=sortState.dir==='asc'?'desc':'asc';
  else { sortState.col=col; sortState.dir=col==='date_iso'?'desc':'asc'; }
  updateSortIcons();
  render();
}
function applyFilters() {
  filters.website=$('f-website').value;
  filters.keyword=$('f-keyword').value.trim();
  filters.days=parseInt($('f-days').value)||3;
  render();
}
function populateWebsiteFilter() {
  const sites=[...new Set(allData.map(a=>a.website))].sort();
  const sel=$('f-website'), cur=sel.value;
  sel.innerHTML='<option value="">All websites</option>'+
    sites.map(w=>`<option value="${esc(w)}"${w===cur?' selected':''}>${esc(w)}</option>`).join('');
}

// \u2500\u2500 Trending \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
function renderTrending() {
  if (!allData.length) {
    $('trending-hero').innerHTML='<div class="lg:col-span-3 py-12 text-center text-on-surface-variant">No data yet \u2014 please wait for news to load.</div>';
    $('trending-grid').innerHTML='';
    return;
  }
  const sorted=[...allData].sort((a,b)=>{
    const la=getLikes(a.link), lb=getLikes(b.link);
    if (lb!==la) return lb-la;
    return (b.date_iso||'')>(a.date_iso||'')?1:-1;
  });
  const top=sorted.slice(0,10);
  const lead=top[0];
  const sec=top.slice(1,3);
  const grid=top.slice(3,9);

  $('trending-hero').innerHTML=`
    <div class="lg:col-span-2 group relative overflow-hidden rounded-2xl bg-surface-container-lowest border border-outline-variant/10 shadow-sm cursor-pointer"
      onclick="window.open('${esc(lead.link)}','_blank')">
      ${lead.image_url?`<div class="aspect-video overflow-hidden"><img src="${esc(lead.image_url)}" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" alt=""></div>`:'<div class="aspect-video bg-gradient-to-br from-primary-fixed to-secondary-container"></div>'}
      <div class="p-8">
        <div class="flex items-center justify-between mb-4">
          <span class="px-3 py-1 bg-primary text-white text-xs font-bold rounded-full uppercase tracking-widest">Top Engagement</span>
          <div class="flex items-center gap-1 text-primary font-bold">
            <span class="material-symbols-outlined text-sm" style="font-variation-settings:'FILL' 1">favorite</span>
            <span>${fmtLikes(getLikes(lead.link))}</span>
          </div>
        </div>
        <h2 class="text-2xl font-headline font-extrabold mb-3 group-hover:text-primary transition-colors leading-snug">${esc(lead.title)}</h2>
        ${lead.summary?`<p class="text-on-surface-variant mb-4 line-clamp-2 text-sm">${esc(lead.summary)}</p>`:''}
        <div class="flex items-center justify-between">
          <span class="text-sm font-bold text-on-surface-variant">${esc(lead.company)} \u00b7 ${esc(lead.website)}</span>
          <span class="flex items-center gap-1 text-sm font-bold text-primary group-hover:translate-x-1 transition-transform">Read Article <span class="material-symbols-outlined text-sm">arrow_forward</span></span>
        </div>
      </div>
    </div>
    <div class="space-y-5">
      ${sec.map(a=>`
        <article onclick="window.open('${esc(a.link)}','_blank')"
          class="p-6 bg-surface-container-low rounded-xl hover:bg-surface-container-high transition-colors cursor-pointer group">
          <div class="flex justify-between items-start mb-3">
            <span class="text-xs font-bold text-on-surface-variant uppercase tracking-tight">${esc(a.website)}</span>
            <div class="flex items-center gap-1 text-primary font-bold text-sm">
              <span class="material-symbols-outlined text-sm" style="font-variation-settings:'FILL' 1">favorite</span>
              <span>${fmtLikes(getLikes(a.link))}</span>
            </div>
          </div>
          <h3 class="text-lg font-headline font-bold mb-2 leading-snug group-hover:text-primary">${esc(a.title)}</h3>
          ${a.summary?`<p class="text-xs text-on-surface-variant line-clamp-2 mb-3">${esc(a.summary)}</p>`:''}
          <span class="text-xs font-black uppercase text-primary flex items-center gap-1">${esc(a.company)} <span class="material-symbols-outlined text-xs">open_in_new</span></span>
        </article>`).join('')}
    </div>`;

  $('trending-grid').innerHTML=grid.map(a=>`
    <div onclick="window.open('${esc(a.link)}','_blank')"
      class="p-6 bg-surface-container-lowest rounded-xl border border-outline-variant/10 shadow-sm flex flex-col cursor-pointer hover:border-primary/30 hover:shadow-md transition-all group">
      <div class="flex justify-between items-start mb-3">
        <span class="text-xs font-bold text-on-surface-variant uppercase tracking-tight">${esc(a.website)}</span>
        <div class="flex items-center gap-1 text-primary font-bold text-xs">
          <span class="material-symbols-outlined text-xs" style="font-variation-settings:'FILL' 1">favorite</span>
          <span>${fmtLikes(getLikes(a.link))}</span>
        </div>
      </div>
      <h4 class="font-headline font-bold text-base mb-3 flex-grow leading-snug group-hover:text-primary transition-colors">${esc(a.title)}</h4>
      <div class="mt-auto flex items-center justify-between pt-3 border-t border-outline-variant/10">
        <span class="text-xs font-semibold text-on-surface-variant">${esc(a.company)}</span>
        <span class="text-xs font-bold text-primary">${fmtDate(a.date_iso)}</span>
      </div>
    </div>`).join('');
}

// \u2500\u2500 AI Insights \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
async function fetchInsights() {
  $('insights-container').innerHTML='<div class="text-center py-16 text-on-surface-variant"><span class="spinner"></span>Generating insights\u2026</div>';
  try {
    const res=await fetch('/api/insights');
    if (!res.ok) throw new Error('HTTP '+res.status);
    const data=await res.json();
    insightsLoaded=true;
    renderInsights(data.insights||[]);
  } catch(e) {
    $('insights-container').innerHTML=`<div class="text-center py-16 text-on-surface-variant">Could not load insights: ${esc(e.message)}</div>`;
  }
}

function renderInsights(insights) {
  if (!insights.length) {
    $('insights-container').innerHTML='<div class="text-center py-16 text-on-surface-variant">No insights yet \u2014 load news first.</div>';
    return;
  }
  const schemes=[
    'bg-primary text-white',
    'bg-secondary-container text-on-secondary-container',
    'bg-surface-container-high text-on-surface',
  ];
  $('insights-container').innerHTML=insights.map((ins,i)=>`
    <article class="rounded-2xl overflow-hidden border border-outline-variant/10 shadow-sm">
      <div class="p-8">
        <div class="flex items-start justify-between gap-4 mb-6">
          <div>
            <span class="inline-block px-3 py-1 rounded-full text-xs font-bold uppercase tracking-widest mb-3 ${schemes[i%3]}">
              Brief #${i+1} \u00b7 ${esc(ins.theme)}
            </span>
            <h2 class="text-2xl font-headline font-extrabold leading-snug">${esc(ins.title)}</h2>
            <p class="text-sm text-on-surface-variant mt-2">${esc(ins.subtitle)}</p>
          </div>
          <span class="text-5xl font-black font-headline text-outline-variant/20 shrink-0 select-none">${String(i+1).padStart(2,'0')}</span>
        </div>
        <p class="text-base leading-relaxed mb-6">${esc(ins.content)}</p>
        ${ins.keywords&&ins.keywords.length?`
          <div class="flex flex-wrap gap-2 mb-6">
            ${ins.keywords.map(k=>{const[b,f]=kwColor(k);return`<span class="kw-tag" style="background:${b};color:${f}">${esc(k)}</span>`;}).join('')}
          </div>`:''}
        <div class="border-t border-outline-variant/20 pt-5">
          <p class="text-xs font-bold uppercase tracking-widest text-on-surface-variant mb-3">Based on these articles</p>
          <div class="space-y-2">
            ${(ins.sources||[]).map(s=>`
              <a href="${esc(s.link)}" target="_blank" rel="noopener"
                class="flex items-center gap-2 text-sm font-medium hover:text-primary transition-colors group">
                <span class="material-symbols-outlined text-xs shrink-0 text-on-surface-variant">article</span>
                <span class="line-clamp-1 group-hover:underline">${esc(s.title)}</span>
                <span class="material-symbols-outlined text-xs shrink-0 text-on-surface-variant ml-auto">open_in_new</span>
              </a>`).join('')}
          </div>
        </div>
      </div>
    </article>`).join('');
}

// \u2500\u2500 Status / Fetch \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
function setStatus(msg, cls) {
  const el=$('status');
  if (!el) return;
  el.className='text-xs font-medium '+(cls==='error'?'text-red-500':'text-on-surface-variant');
  el.innerHTML=msg;
}

async function fetchNews(forceRefresh) {
  if ($('refresh-btn')) $('refresh-btn').disabled=true;
  if (forceRefresh) insightsLoaded=false;
  setStatus('<span class="spinner"></span>Fetching\u2026','loading');
  try {
    const res=await fetch(forceRefresh?'/api/news?refresh=1':'/api/news');
    if (!res.ok) throw new Error('HTTP '+res.status);
    const data=await res.json();
    if (data.status==='loading'||data.status==='refreshing') {
      if (data.articles&&data.articles.length>0) {
        allData=data.articles;
        populateWebsiteFilter();
        render();
        const ts=data.timestamp?new Date(data.timestamp).toLocaleTimeString():'';
        setStatus(`<span class="spinner"></span>Updating\u2026 (cached ${ts})`,'loading');
      } else {
        $('tbody').innerHTML='<tr><td colspan="7" class="py-12 text-center text-on-surface-variant"><span class="spinner"></span>Scraping 65 sources\u2026 (~2 min)</td></tr>';
        setStatus('<span class="spinner"></span>Scraping\u2026','loading');
      }
      clearTimeout(pollTimer);
      pollTimer=setTimeout(()=>fetchNews(false),8000);
    } else {
      clearTimeout(pollTimer);
      allData=data.articles||[];
      populateWebsiteFilter();
      render();
      const ts=data.timestamp?new Date(data.timestamp).toLocaleTimeString():'';
      setStatus(`${allData.length} articles \u00b7 ${ts}`);
      if ($('refresh-btn')) $('refresh-btn').disabled=false;
      if (currentTab==='insights') { insightsLoaded=false; fetchInsights(); }
    }
  } catch(e) {
    setStatus('Error: '+esc(e.message),'error');
    $('tbody').innerHTML=`<tr><td colspan="7" class="py-12 text-center text-red-400">Failed: ${esc(e.message)}</td></tr>`;
    if ($('refresh-btn')) $('refresh-btn').disabled=false;
  }
}

// \u2500\u2500 Init \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
const _d=new Date();
const _tl=$('today-label');
if (_tl) _tl.textContent=_d.toLocaleDateString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric'});
updateSortIcons();
switchTab('latest');
fetchNews(false);
</script>
</body>
</html>"""


# ─────────────────────── Flask routes ────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/insights')
def get_insights():
    articles = [a for a in (CACHE['data'] or [])
                if a.get('summary') and len(a.get('summary', '')) > 30][:40]
    if not articles:
        return jsonify({'insights': [], 'status': 'no_data'})

    kw_count = Counter()
    kw_articles = {}
    for a in articles:
        for kw in a.get('keywords', []):
            kw_count[kw] += 1
            kw_articles.setdefault(kw, []).append(a)

    insights = []
    used = set()
    for kw, _ in kw_count.most_common(12):
        if len(insights) >= 3:
            break
        theme_arts = [a for a in kw_articles[kw] if a['link'] not in used][:6]
        if len(theme_arts) < 2:
            continue
        for a in theme_arts:
            used.add(a['link'])
        companies = list({a['company'] for a in theme_arts
                          if a.get('company') not in ('Various', '', None)})[:4]
        summaries = [a['summary'] for a in theme_arts if a.get('summary')]
        all_kws = list({k for a in theme_arts for k in a.get('keywords', [])})[:8]
        co_str = ', '.join(companies[:3]) if companies else 'multiple organizations'
        body = ' '.join(summaries[:3])
        if len(body) > 600:
            body = body[:600].rsplit(' ', 1)[0] + '…'
        intro = (f"Analysis of {len(theme_arts)} recent articles highlights strong momentum "
                 f"around \"{kw}\". Coverage spans {co_str}. ")
        insights.append({
            'theme': kw.title(),
            'title': f"Intelligence Brief: The State of {kw.title()} in AI",
            'subtitle': f"Synthesized from {len(theme_arts)} sources · {', '.join(companies[:2]) if companies else 'multiple sources'}",
            'content': intro + body,
            'companies': companies,
            'article_count': len(theme_arts),
            'sources': [{'title': a['title'], 'link': a['link'], 'website': a['website']}
                        for a in theme_arts[:4]],
            'keywords': all_kws,
        })
    return jsonify({'insights': insights, 'status': 'ready'})


@app.route('/health')
def health():
    import resource
    try:
        mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    except Exception:
        mem_mb = -1
    return jsonify({
        'status': 'ok',
        'job_running': JOB['running'],
        'cache_articles': len(CACHE['data']) if CACHE['data'] else 0,
        'cache_age_seconds': int((datetime.now() - CACHE['timestamp']).total_seconds()) if CACHE['timestamp'] else None,
        'memory_mb': mem_mb,
    })


def run_scrape(days=3):
    """Scrape all sources in background and update CACHE when done."""
    if JOB['running']:
        return
    JOB['running'] = True
    all_articles = []
    pool = ThreadPoolExecutor(max_workers=5)
    try:
        futures = {pool.submit(process_source, src, days): src for src in SOURCES}
        try:
            for future in as_completed(futures, timeout=180):
                src = futures[future]
                try:
                    all_articles.extend(future.result(timeout=30))
                except Exception as e:
                    logger.debug(f"Source {src.get('website','?')} failed: {e}")
        except Exception as e:
            logger.warning(f"Scrape timeout: {e}, {len(all_articles)} articles collected")
    except Exception as e:
        logger.error(f"ThreadPool failed: {e}")
    finally:
        pool.shutdown(wait=False)  # Don't block on stuck threads
        all_articles.sort(key=lambda x: x.get('date_iso', ''), reverse=True)
        CACHE['data'] = all_articles
        CACHE['timestamp'] = datetime.now()
        JOB['running'] = False
        logger.info(f"Scrape done: {len(all_articles)} articles")


@app.route('/api/news')
def get_news():
    force = bool(request.args.get('refresh'))
    days = int(request.args.get('days', 3))

    # Serve cache if still fresh
    if not force and CACHE['data'] is not None and CACHE['timestamp']:
        age = (datetime.now() - CACHE['timestamp']).total_seconds()
        if age < CACHE['ttl_seconds']:
            return jsonify({
                'articles': CACHE['data'],
                'status': 'ready',
                'from_cache': True,
                'timestamp': CACHE['timestamp'].isoformat(),
            })

    # Start background scrape if not already running
    if not JOB['running']:
        threading.Thread(target=run_scrape, args=(days,), daemon=True).start()

    # Return immediately — stale cache or loading
    if CACHE['data'] is not None:
        return jsonify({
            'articles': CACHE['data'],
            'status': 'refreshing',
            'from_cache': True,
            'timestamp': CACHE['timestamp'].isoformat(),
        })
    return jsonify({'articles': [], 'status': 'loading', 'from_cache': False, 'timestamp': ''})


if __name__ == '__main__':
    today = date.today()
    print("=" * 55)
    print("  AI News Dashboard  (last 3 days)")
    print(f"  Date range: {(today - timedelta(days=2)).isoformat()} → {today.isoformat()}")
    print("  Open: http://localhost:5000")
    print("=" * 55)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
