#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль генерации выходных файлов в формате Markdown.

Этот модуль отвечает за создание структурированных Markdown файлов с метаданными
о книгах, включая YAML frontmatter, теги, обложки и аннотации. Также предоставляет
функции для скачивания и сохранения обложек книг.

Автор: Book Parser Project
Лицензия: MIT
"""

import re
from pathlib import Path
import requests
from urllib.parse import urlparse


def clean_series_or_title(value):
    """
    Удаляет упоминания аудио из названия книги или серии, но сохраняет номера (цифры) на конце.

    Примеры:
        "Название 5 (аудио)"              -> "Название 5"
        "Название 5 [Аудиокнига]"         -> "Название 5"
        "Хозяин дубравы. Том 1. Желудь. Аудиокнига" -> "Хозяин дубравы. Том 1. Желудь"
    """
    if not value:
        return ''
    # Хвост вида: пробелы/точка/тире + (опциональные скобки) + "аудио"/"аудиокнига"/"аудио-версия"/"audio-version"
    # в самом конце строки. Перед этим хвостом могут быть цифры (номер тома) — их сохраняем.
    audio_pattern = (
        r"[\s\.\-–—]*"                          # пробелы/точки/тире перед пометкой
        r"(?:\(|\[)?\s*"                        # опциональные открывающие скобки
        r"(?:аудио(книга|\-версия)?|аудио-версия|аудиокнига|audio(?:-version)?)"  # варианты слова "аудио"
        r"\s*(?:\)|\])?"                        # опциональные закрывающие скобки
        r"\s*$"                                 # до конца строки
    )
    # Удаляем только аудио-хвост в конце строки
    result = re.sub(audio_pattern, '', value, flags=re.IGNORECASE)
    # Стандартная чистка пробелов по краям
    result = result.strip()
    # Если после удаления аудио не осталось букв/цифр, возвращаем исходное значение
    return result or value


def normalize_authors(authors):
    """
    Приводит список/строку авторов к списку уникальных имен (с разделением по запятым).
    """
    if not authors:
        return []

    processed_authors = []
    for author in authors:
        if isinstance(author, str):
            # Поддержка входных строк вида "Имя1, Имя2"
            split_authors = [a.strip() for a in author.split(',') if a.strip()]
            processed_authors.extend(split_authors)
        else:
            processed_authors.append(author)

    seen = set()
    unique = []
    for author in processed_authors:
        author_str = str(author).strip()
        if author_str and author_str not in seen:
            seen.add(author_str)
            unique.append(author_str)
    return unique


def generate_book_file(book_data, cover_path=None):
    """
    Генерирует полное содержимое Markdown файла для книги.
    
    Создает структурированный файл с YAML frontmatter, тегами, обложкой,
    комментариями, жанрами и аннотацией. Автоматически обрабатывает множественных
    авторов, объединяет рейтинги с сайта и из файла.
    
    Args:
        book_data (dict): Словарь с данными о книге. Ожидаемые ключи:
            - date (str): Дата прочтения
            - title (str): Название книги
            - series (str): Название серии (опционально)
            - authors (list): Список авторов
            - rating_from_site (str): Рейтинг с сайта (опционально)
            - rating_from_file (str): Рейтинг из файла (опционально)
            - useful (str): Полезность книги (опционально)
            - type (str): Тип издания ('Book' или 'Audiobook')
            - reader (list): Список чтецов для аудиокниг (опционально)
            - cover (str): Имя файла обложки (опционально)
            - url (str): URL книги
            - comment (str): Комментарий (опционально)
            - genres (list): Список жанров (опционально)
            - annotation (str): Аннотация (опционально)
        cover_path (str или Path, optional): Путь к файлу обложки.
            Если указан, имя файла будет использовано в frontmatter.
    
    Returns:
        str: Полное содержимое Markdown файла с YAML frontmatter
        
    Example:
        >>> book_data = {
        ...     'title': 'Название',
        ...     'authors': ['Автор 1', 'Автор 2'],
        ...     'date': '2025-12-01',
        ...     'rating_from_site': '👍 97',
        ...     'rating_from_file': '⭐️⭐️⭐️⭐️'
        ... }
        >>> content = generate_book_file(book_data)
        >>> print(content[:100])
    """
    # YAML frontmatter
    frontmatter = '---\n'
    frontmatter += f"date: {book_data.get('date', '')}\n"

    # Чистим title (но НЕ удаляем номер части!)
    orig_title = book_data.get('title', 'Без названия')
    title_clean = clean_series_or_title(orig_title)
    frontmatter += f"title: {title_clean}\n"
    
    # Серия (с очисткой от аудиометок)
    series = clean_series_or_title(book_data.get('series', '').strip())
    if series:
        # Убираем кавычки (используем Unicode escape sequences)
        quotes_pattern = r'["\'\u00AB\u00BB\u201E\u201C\u201D\u2018\u2019\u201A\u201B\u2039\u203A]'
        series = re.sub(quotes_pattern, '', series).strip()
        if series:
            frontmatter += f"series: {series}\n"
    
    # Авторы
    authors = book_data.get('authors', [])
    if not authors and book_data.get('author'):
        authors = [book_data['author']]

    unique_authors = normalize_authors(authors)
    
    if unique_authors:
        frontmatter += 'author:\n'
        for author in unique_authors:
            frontmatter += f'  - {author}\n'
    
    # Рейтинг: объединяем рейтинг с сайта и из файла
    # Куда что: рейтинг с сайта = из book_data['rating_from_site'] (если есть), рейтинг из файла = book_data['rating_from_file']
    r1 = book_data.get('rating_from_site', '')
    r2 = book_data.get('rating_from_file', '')
    # Преобразуем в строки и очищаем от пробелов
    if r1 is None:
        r1 = ''
    else:
        r1 = str(r1).strip()
    if r2 is None:
        r2 = ''
    else:
        r2 = str(r2).strip()
    
    rating = ''
    if r1 and r2:
        rating = f"{r1} / {r2}"
    elif r1:
        rating = r1
    elif r2:
        rating = r2
    # если пусто, будет null
    if rating:
        frontmatter += f"rating: {rating}\n"
    else:
        frontmatter += "rating: null\n"

    # Поле useful
    useful = book_data.get('useful', '').strip()
    if useful:
        frontmatter += f"useful: {useful}\n"

    # Статус
    frontmatter += "status: Finished\n"
    
    # Тип
    book_type = book_data.get('type', 'Book')
    frontmatter += f"type: {book_type}\n"
    
    # Чтец (для аудиокниг)
    if book_type == 'Audiobook':
        readers = book_data.get('reader', [])
        if readers:
            if len(readers) == 1:
                frontmatter += f"reader: {readers[0]}\n"
            else:
                frontmatter += 'reader:\n'
                for reader in readers:
                    frontmatter += f"  - {reader}\n"
    
    # Обложка
    if cover_path:
        cover_filename = Path(cover_path).name
        frontmatter += f'cover: "{cover_filename}"\n'
    else:
        frontmatter += 'cover: ""\n'
    
    # URL
    url = book_data.get('url', '')
    frontmatter += f"URL: {url if url else 'null'}\n"
    frontmatter += '---\n\n'
    
    # Контент
    content = ''
    
    # Теги
    tags = []
    if book_type == 'Audiobook':
        tags.append('#Audiobook')
    else:
        tags.append('#Book')
    
    # Добавляем теги авторов
    for author in unique_authors:
        author_tag = author.replace(' ', '').replace('.', '').replace(',', '')
        tags.append(f'#{author_tag}')
    
    # Тег серии
    if series:
        series_tag = series.replace(' ', '').replace('.', '').replace(',', '')
        tags.append(f'#{series_tag}')
    
    # Тег чтеца
    if book_type == 'Audiobook' and readers:
        for reader in readers:
            reader_tag = reader.replace(' ', '').replace('.', '').replace(',', '')
            tags.append(f'#{reader_tag}')
    
    if tags:
        content += ', '.join(tags) + '\n\n'
    
    # Изображение обложки
    if cover_path:
        cover_filename = Path(cover_path).name
        content += f'![[{cover_filename}]]\n\n'
    
    # Комментарий к прочитанному
    comment = book_data.get('comment', '').strip()
    content += 'Комментарий к прочитанному: '
    if comment:
        content += comment
    content += '\n\n'
    
    # Жанры
    genres = book_data.get('genres', [])
    if genres:
        content += f"Жанр: {', '.join(genres)}\n\n"
    
    # Аннотация
    annotation = book_data.get('annotation', '').strip()
    if annotation:
        annotation = clean_annotation(annotation)
        content += 'Аннотация:\n'
        content += f"{annotation}\n"
    
    return frontmatter + content


def clean_annotation(text):
    """
    Очищает аннотацию от HTML тегов и лишних символов.
    
    Удаляет HTML теги, заменяет HTML entities на обычные символы,
    удаляет управляющие символы и нормализует пробелы и переносы строк.
    
    Args:
        text (str): Исходный текст аннотации (может содержать HTML)
        
    Returns:
        str: Очищенный текст без HTML тегов и лишних символов
        
    Example:
        >>> clean_annotation('<p>Текст с <b>HTML</b> тегами</p>')
        'Текст с HTML тегами'
    """
    if not text:
        return ''
    
    # Удаляем HTML теги
    text = re.sub(r'<[^>]*>', '', text)
    
    # Заменяем HTML entities
    text = text.replace('&nbsp;', ' ')
    text = text.replace('&quot;', '"')
    text = text.replace('&apos;', "'")
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    
    # Удаляем управляющие символы
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)
    
    # Нормализуем переносы строк
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\r', '\n', text)
    
    # Удаляем лишние пробелы и переносы
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n[ \t]+', '\n', text)
    
    return text.strip()


def save_cover_image(cover_url, book_data, output_dir):
    """
    Скачивает и сохраняет обложку книги с указанного URL.
    
    Автоматически определяет расширение изображения из URL, генерирует
    имя файла на основе авторов и названия книги, скачивает изображение
    и сохраняет его в указанную папку.
    
    Args:
        cover_url (str): URL изображения обложки
        book_data (dict): Словарь с данными о книге для генерации имени файла.
            Используются ключи:
            - authors (list): Список авторов
            - title (str): Название книги
            - seriesNumber (str): Номер в серии (опционально)
        output_dir (str или Path): Папка для сохранения обложки
    
    Returns:
        Path или None: Путь к сохраненному файлу обложки или None,
            если скачивание не удалось
            
    Example:
        >>> book_data = {'authors': ['Автор'], 'title': 'Название'}
        >>> path = save_cover_image('https://example.com/cover.jpg', book_data, './covers')
        >>> print(path)
        PosixPath('./covers/Автор_-_Название.jpg')
    """
    if not cover_url:
        return None
    
    try:
        # Определяем расширение
        ext = get_image_extension_from_url(cover_url)
        
        # Генерируем имя файла
        authors = book_data.get('authors', [])
        if not authors and book_data.get('author'):
            authors = [book_data['author']]

        unique_authors = normalize_authors(authors)
        
        # Для имени файла используем всех авторов через запятую
        if unique_authors:
            author = ', '.join(unique_authors)
        else:
            author = 'Unknown'
        
        title = book_data.get('title', 'book')
        series_number = book_data.get('seriesNumber', '')
        
        # Формируем базовое имя файла: автор - название
        base_name = f"{author} - {title}"
        # Добавляем номер серии, если есть (до sanitize_filename, чтобы пробел был заменен на подчеркивание)
        if series_number:
            base_name += f" {series_number}"
        # Очищаем имя файла от недопустимых символов (заменяет пробелы на подчеркивания)
        filename = sanitize_filename(base_name)
        filename += f".{ext}"
        
        # Скачиваем изображение
        response = requests.get(cover_url, timeout=30, stream=True)
        response.raise_for_status()
        
        # Сохраняем
        output_path = Path(output_dir) / filename
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return output_path
        
    except Exception as e:
        print(f"Ошибка при сохранении обложки: {e}")
        return None


def get_image_extension_from_url(url):
    """Определяет расширение изображения из URL"""
    if not url:
        return 'png'
    
    try:
        parsed = urlparse(url)
        url_lower = url.lower()
        
        # Проверяем параметр format
        if 'format=' in url_lower:
            if 'format=webp' in url_lower:
                return 'webp'
            elif 'format=jpg' in url_lower or 'format=jpeg' in url_lower:
                return 'jpg'
            elif 'format=png' in url_lower:
                return 'png'
            elif 'format=gif' in url_lower:
                return 'gif'
        
        # Проверяем расширение в пути
        path_lower = parsed.path.lower()
        if '.webp' in path_lower:
            return 'webp'
        elif '.jpg' in path_lower or '.jpeg' in path_lower:
            return 'jpg'
        elif '.png' in path_lower:
            return 'png'
        elif '.gif' in path_lower:
            return 'gif'
        
        return 'png'  # По умолчанию
    except:
        return 'png'


def sanitize_filename(name):
    """Очищает имя файла от недопустимых символов"""
    # Заменяем недопустимые символы
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Заменяем пробелы на подчеркивания
    name = re.sub(r'\s+', '_', name)
    # Ограничиваем длину
    return name[:100].strip('_')

