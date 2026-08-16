#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль парсинга входного файла со списком книг.

Этот модуль обрабатывает Markdown файлы со списком книг для последующего парсинга.
Поддерживает формат строк с датой, URL, рейтингом, полезностью и комментарием.

Автор: Book Parser Project
Лицензия: MIT
"""

import re
from pathlib import Path


def parse_input_file(file_path):
    """
    Парсит входной Markdown файл со списком книг.

    Читает файл построчно, извлекает данные о каждой книге и возвращает
    список словарей с информацией. Пропускает пустые строки и комментарии
    (строки, начинающиеся с #).

    Args:
        file_path (str или Path): Путь к файлу со списком книг

    Returns:
        list: Список словарей с данными о книгах. Каждый словарь содержит:
            - date (str): Дата в формате YYYY-MM-DD
            - url (str): URL книги на litres.ru или author.today
            - rating (str): Рейтинг в формате звездочек (⭐️) или None
            - useful (str): Полезность книги ('yes'/'no') или None
            - comment (str): Комментарий к книге или пустая строка

    Raises:
        FileNotFoundError: Если указанный файл не существует

    Example:
        >>> books = parse_input_file('books_list.md')
        >>> print(books[0]['url'])
        'https://author.today/audiobook/...'

    Формат строки в файле:
        YYYY-MM-DD, URL, X, yes|no, комментарий

    Примеры:
        2025-12-01, https://author.today/audiobook/497189, 4, no, Комментарий
        2025-11-27, https://www.litres.ru/book/..., 5, yes
        2025-10-15, https://author.today/work/..., , , Просто комментарий
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    books = []

    with open(file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()

            # Пропускаем пустые строки и комментарии
            if not line or line.startswith('#'):
                continue

            try:
                # Пробуем новый формат (с запятыми)
                book_data = parse_line_new_format(line)
                if not book_data:
                    # Если не сработало, пробуем старый формат
                    book_data = parse_line_old_format(line)

                if book_data:
                    books.append(book_data)
            except Exception as e:
                print(f"Ошибка парсинга строки {line_num}: {e}")
                print(f"  Строка: {line}")
                continue

    return books


def parse_line_new_format(line):
    """
    Парсит строку в новом формате: YYYY-MM-DD, URL, X, yes|no, комментарий
    """
    # Регулярное выражение для нового формата
    # Формат: YYYY-MM-DD, URL, X, yes|no, комментарий
    pattern = r'^(\d{4}-\d{2}-\d{2}),\s*(https?://[^\s,]+[^\s]*)(?:,\s*([^,]*?)(?:,\s*(yes|no)?(?:,\s*(.*))?)?)?$'

    match = re.match(pattern, line)
    if not match:
        return None

    date, url, rating, useful, comment = match.groups()

    # Очищаем значения
    date = date.strip() if date else ''
    url = url.strip() if url else ''
    rating = rating.strip() if rating else None
    useful = useful.strip() if useful else None
    comment = comment.strip() if comment else ''

    # Конвертируем рейтинг в звездочки
    if rating:
        try:
            rating_num = float(rating.strip())
            stars_count = min(int(round(rating_num)), 5)
            rating = '⭐️' * stars_count
        except (ValueError, TypeError):
            pass

    return {
        'date': date,
        'url': url,
        'rating': rating,
        'useful': useful,
        'comment': comment
    }


def parse_line_old_format(line):
    """
    Парсит строку в старом формате для обратной совместимости:
    YYYY-MM-DD URL Rating: X / Useful: yes|no / комментарий
    """
    # Регулярные выражения для старого формата

    # Дата в начале строки в формате YYYY-MM-DD
    date_pattern = r'^(\d{4}-\d{2}-\d{2})\s+'

    # URL начинается с http:// или https:// и продолжается до пробела
    url_pattern = r'(https?://[^\s]+)'

    # Рейтинг в формате "Rating: X" (опционально, может быть до или после "/")
    rating_pattern = r'Rating:\s*([^/]+?)(?:\s*/\s*|$)'

    # Полезность в формате "Useful: yes|no" (опционально)
    useful_pattern = r'Useful:\s*([^/]+?)(?:\s*/\s*|$)'

    # Извлекаем дату
    date_match = re.match(date_pattern, line)
    if not date_match:
        return None

    date = date_match.group(1)
    remaining = line[date_match.end():].strip()

    # Извлекаем URL
    url_match = re.search(url_pattern, remaining)
    if not url_match:
        return None

    url = url_match.group(1)
    remaining = remaining[:url_match.start()] + \
        remaining[url_match.end():].strip()

    # Извлекаем Rating
    rating = None
    rating_match = re.search(rating_pattern, remaining, re.IGNORECASE)
    if rating_match:
        rating = rating_match.group(1).strip()
        remaining = remaining[:rating_match.start()] + \
            remaining[rating_match.end():].strip()

    # Извлекаем Useful
    useful = None
    useful_match = re.search(useful_pattern, remaining, re.IGNORECASE)
    if useful_match:
        useful = useful_match.group(1).strip()
        remaining = remaining[:useful_match.start()] + \
            remaining[useful_match.end():].strip()

    # Остальное - комментарий
    comment = remaining.strip()

    # Убираем лишние разделители из начала комментария
    comment = re.sub(r'^\s*/\s*', '', comment)

    # Конвертируем рейтинг в звездочки
    if rating:
        try:
            rating_num = float(rating.strip())
            stars_count = min(int(round(rating_num)), 5)
            rating = '⭐️' * stars_count
        except (ValueError, TypeError):
            pass

    return {
        'date': date,
        'url': url,
        'rating': rating,
        'useful': useful,
        'comment': comment
    }


def parse_line(line):
    """
    Парсит одну строку со списком книг и извлекает данные.
    Поддерживает оба формата: старый и новый.
    """
    # Сначала пробуем новый формат
    book_data = parse_line_new_format(line)
    if book_data:
        return book_data

    # Если не сработало, пробуем старый формат
    return parse_line_old_format(line)
