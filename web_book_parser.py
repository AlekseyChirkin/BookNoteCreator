#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Book Note Creator — local web app for adding books to Obsidian.

Run:
    python web_book_parser.py

Portable build:
    build_portable.bat
"""

import atexit
import os
import re
import webbrowser
import os
import signal
import threading
import time
from threading import Lock, Timer
from flask import Flask, jsonify, render_template, request

from app_paths import configure_playwright, output_dir, static_dir, templates_dir
from output_generator import (
    generate_book_file,
    normalize_authors,
    sanitize_filename,
    save_cover_image,
)
from parser import BookParser

configure_playwright()

app = Flask(
    __name__,
    template_folder=str(templates_dir()),
    static_folder=str(static_dir()),
)

OUTPUT_DIR = output_dir()
_save_lock = Lock()
parser = BookParser()

_URL_LINE_RE = re.compile(r'^\s*URL:\s*(\S+)\s*$', re.IGNORECASE)


def close_parser() -> None:
    if parser:
        parser.close()


atexit.register(close_parser)


def _find_existing_md_by_url(url: str):
    url = (url or '').strip()
    if not url:
        return None

    try:
        for md_path in OUTPUT_DIR.glob('*.md'):
            try:
                with open(md_path, 'r', encoding='utf-8') as f:
                    for _ in range(80):
                        line = f.readline()
                        if not line:
                            break
                        match = _URL_LINE_RE.match(line)
                        if match and match.group(1).strip() == url:
                            return md_path
            except OSError:
                continue
    except OSError:
        return None
    return None


def _is_retryable_error(message: str) -> bool:
    msg = (message or '').lower()
    retry_keywords = [
        'timed out', 'timeout', 'connection', 'dns', 'temporarily', 'refused',
        'network', 'permission denied', 'access is denied', 'resource busy',
        'device or resource busy', 'busy',
    ]
    return any(keyword in msg for keyword in retry_keywords)


def _save_book_internal(url: str, date: str, rating_str: str, useful: str, comment: str):
    url = (url or '').strip()
    date = (date or '').strip()
    rating_str = (rating_str or '').strip()
    useful = (useful or '').strip()
    comment = (comment or '').strip()

    if not url or not date:
        return None, {'error': 'Book URL and read date are required.', 'retryable': False}, 400

    with _save_lock:
        existing_md = _find_existing_md_by_url(url)
        if existing_md and existing_md.exists():
            payload = {
                'status': 'exists',
                'path': str(existing_md),
                'filename': existing_md.name,
                'warnings': ['This book is already saved. Duplicate was not created.'],
                'missingFields': [],
                'retryable': False,
            }
            return None, payload, 200

    rating_stars = ''
    if rating_str:
        try:
            rating_num = int(rating_str)
            if 1 <= rating_num <= 5:
                rating_stars = '⭐' * rating_num
        except ValueError:
            pass

    try:
        site_data = parser.parse_url(url)
        if site_data.get('error'):
            err = str(site_data['error'])
            return None, {'error': err, 'retryable': _is_retryable_error(err)}, 400
    except Exception as exc:
        err = f'Parsing failed: {exc}'
        return None, {'error': err, 'retryable': _is_retryable_error(err)}, 500

    file_data = {
        'date': date,
        'url': url,
        'rating': rating_stars,
        'useful': useful if useful in ('yes', 'no') else '',
        'comment': comment,
    }

    combined = parser.combine_book_data(site_data, file_data)

    warnings = []
    missing_fields = []
    if not (combined.get('title') or '').strip():
        missing_fields.append('title')
        warnings.append('Could not determine the book title.')
    if not combined.get('authors'):
        missing_fields.append('authors')
        warnings.append('Could not determine the author(s).')
    if not (combined.get('annotation') or '').strip():
        missing_fields.append('annotation')
        warnings.append('Could not extract the annotation.')
    if not (combined.get('coverUrl') or '').strip():
        missing_fields.append('coverUrl')
        warnings.append('Could not extract the cover image URL.')
    if not rating_str:
        missing_fields.append('rating')
        warnings.append('Personal rating was not provided.')
    if useful not in ('yes', 'no'):
        missing_fields.append('useful')
        warnings.append('Recommendation field was not provided.')
    if not comment:
        missing_fields.append('comment')
        warnings.append('Comment was not provided.')

    cover_path = None
    cover_url = combined.get('coverUrl', '')
    if cover_url:
        try:
            cover_path = save_cover_image(cover_url, combined, OUTPUT_DIR)
        except Exception as exc:
            print(f'Cover save error: {exc}')

    md_content = generate_book_file(combined, cover_path)

    if cover_path:
        base_name = cover_path.stem
    else:
        authors = combined.get('authors', [])
        if not authors and combined.get('author'):
            authors = [combined['author']]
        unique_authors = normalize_authors(authors)
        author_str = ', '.join(unique_authors) if unique_authors else 'Unknown'
        title = combined.get('title', 'book')
        base_name = f"{author_str} - {title}"
        series_number = combined.get('seriesNumber', '')
        if series_number:
            base_name += f" {series_number}"

    filename = sanitize_filename(base_name) + '.md'
    md_path = OUTPUT_DIR / filename

    with _save_lock:
        existing_md = _find_existing_md_by_url(url)
        if existing_md and existing_md.exists():
            payload = {
                'status': 'exists',
                'path': str(existing_md),
                'filename': existing_md.name,
                'warnings': ['This book is already saved. Duplicate was not created.'],
                'missingFields': [],
                'retryable': False,
            }
            return None, payload, 200

        if md_path.exists():
            stem = md_path.stem
            suffix = md_path.suffix
            for number in range(2, 1000):
                candidate = OUTPUT_DIR / f"{stem} ({number}){suffix}"
                if not candidate.exists():
                    md_path = candidate
                    break

        try:
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(md_content)
        except OSError as exc:
            err = f'Failed to write file: {exc}'
            return None, {'error': err, 'retryable': _is_retryable_error(err)}, 500

    return combined, {
        'status': 'saved',
        'path': str(md_path),
        'filename': md_path.name,
        'warnings': warnings,
        'missingFields': missing_fields,
        'retryable': False,
    }, 200


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/shutdown', methods=['POST'])
def shutdown():
    try:
        close_parser()
    except Exception:
        pass

    def _shutdown():
        time.sleep(0.5)  # увеличили с 0.3 до 0.5 секунды
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({'status': 'shutting_down'})


@app.route('/parse', methods=['POST'])
def parse_book():
    data = request.get_json(silent=True) or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': 'Book URL is required.'}), 400

    try:
        book_info = parser.parse_url(url)
        if book_info.get('error'):
            return jsonify({'error': book_info['error']}), 400

        preview = {
            'title': book_info.get('title', ''),
            'authors': book_info.get('authors', []),
            'annotation': book_info.get('annotation', ''),
            'coverUrl': book_info.get('coverUrl', ''),
            'genres': book_info.get('genres', []),
            'type': book_info.get('type', 'Book'),
            'reader': book_info.get('reader', []),
            'series': book_info.get('series', ''),
            'seriesNumber': book_info.get('seriesNumber', ''),
        }
        return jsonify(preview)
    except Exception as exc:
        return jsonify({'error': f'Parsing failed: {exc}'}), 500


@app.route('/save', methods=['POST'])
def save_book():
    data = request.get_json(silent=True) or {}
    _, payload, status = _save_book_internal(
        url=data.get('url', ''),
        date=data.get('date', ''),
        rating_str=data.get('rating', ''),
        useful=data.get('useful', ''),
        comment=data.get('comment', ''),
    )
    return jsonify(payload), status


def open_browser() -> None:
    Timer(1, lambda: webbrowser.open('http://127.0.0.1:5000')).start()


if __name__ == '__main__':
    open_browser()
    app.run(host='127.0.0.1', port=5000, debug=False)
    if __name__ == '__main__':
        open_browser()
        try:
            app.run(host='127.0.0.1', port=5000, debug=False)
        except KeyboardInterrupt:
            pass
        finally:
            close_parser()
            os._exit(0)
