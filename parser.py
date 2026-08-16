#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль парсинга данных о книгах с веб-сайтов.

Ключевые возможности:
- Поддержка litres.ru и author.today (с Playwright для рендеринга JS)
- Извлечение названия, авторов, серии, аннотации, обложки, рейтинга, жанров,
  чтецов и типа издания
- Объединение данных с сайта и из входного файла

Автор: Book Parser Project
Лицензия: MIT
"""

import json
import re
import sys
import traceback
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
try:
    from playwright.sync_api import Browser, Playwright, sync_playwright
except ModuleNotFoundError:
    # Делаем Playwright опциональным: автор.today потребует установки,
    # litres.ru продолжит работать без него.
    Browser = None  # type: ignore[assignment]
    Playwright = None  # type: ignore[assignment]
    sync_playwright = None  # type: ignore[assignment]

AUTHOR_TODAY_LIKE_SELECTOR = "span.like-count"


class BookParser:
    """
    Класс для парсинга метаданных книг с веб-сайтов (litres.ru, author.today).

    Использует комбинированный подход: Playwright для динамического рендеринга JS 
    (автор.today) и requests + BeautifulSoup для быстрого парсинга статического HTML (litres.ru).

    Attributes:
        session (requests.Session): HTTP-секация с имитацией браузера.
        _browser (Browser, optional): Кэшированный экземпляр Chromium.
        _playwright (Playwright, optional): Экземпляр управления браузером.
    """

    def __init__(self):
        """
        Инициализирует парсер с настройками HTTP-сессии.

        Устанавливает User-Agent для имитации браузера, что необходимо
        для корректной работы с некоторыми сайтами.
        """
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        # Лениво создаваемый браузер Playwright для повторного использования
        # между множеством URL — ускоряет пакетную обработку author.today.
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    @staticmethod
    def _safe_print(message: str) -> None:
        """
        Печать, которая не падает из-за кодировки консоли Windows.
        Нужна, чтобы ошибки Playwright не ломали fallback на requests.
        """
        try:
            print(message)
        except UnicodeEncodeError:
            # Превращаем не-ASCII в \uXXXX, чтобы гарантированно напечатать
            safe = message.encode('ascii', errors='backslashreplace').decode('ascii')
            print(safe)

    def _ensure_browser(self) -> Browser:
        """
        Лениво поднимает браузер Playwright и возвращает его экземпляр.

        Почему лениво:
        - author.today требует JS-рендеринг;
        - запуск браузера дорогой, поэтому мы создаём его один раз и переиспользуем
          между множеством URL (особенно важно в пакетной обработке).
        """
        if self._browser:
            return self._browser
        if sync_playwright is None:
            raise RuntimeError("Playwright не установлен. Установите его или выполните 'playwright install chromium'.")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def _get_rendered_soup(self, url: str, wait_time_ms: int = 1500) -> BeautifulSoup:
        """
        Возвращает отрендеренный HTML (в основном нужен для author.today).

        Алгоритм:
        - пытаемся отрендерить страницу через Playwright (chromium, headless)
        - если Playwright недоступен/браузеры не установлены/ошибка навигации —
          делаем fallback на `requests` (статичный HTML)

        Примечание:
        - fallback может дать менее полный HTML на JS-страницах, но приложение
          продолжит работать и не сорвёт сохранение остальных книг.
        """
        try:
            browser = self._ensure_browser()
            page = browser.new_page()
            page.goto(url, timeout=60000)
            page.wait_for_timeout(wait_time_ms)
            html = page.content()
            page.close()
            return BeautifulSoup(html, 'html.parser')
        except Exception as e:
            # Playwright иногда возвращает многострочный "баннер" — логируем кратко,
            # чтобы не засорять вывод и не ломать fallback.
            err_line = str(e).splitlines()[0] if str(e) else repr(e)
            self._safe_print(f"Не удалось получить рендер через Playwright для {url}: {err_line}")
            try:
                # Fallback: статичный HTML. Лучше что-то, чем ничего.
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                response.encoding = 'utf-8'
                return BeautifulSoup(response.text, 'html.parser')
            except Exception:
                raise

    def close(self) -> None:
        """
        Закрывает браузер Playwright, если он был запущен.

        В реальных сценариях завершения на Windows иногда возможна ошибка greenlet
        (зависит от контекста/потоков). Поэтому закрытие сделано максимально
        "безопасным": ошибки подавляются, чтобы не шуметь трассировкой при exit.
        """
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                # Может падать greenlet.error при завершении не из того потока/контекста.
                pass
            self._playwright = None

    def parse_url(self, url: str) -> Dict[str, Any]:
        """
        Парсит страницу книги и возвращает словарь с метаданными.

        Автоматически определяет источник (litres.ru или author.today) и использует
        соответствующий метод парсинга. Обрабатывает сетевые ошибки.

        Args:
            url (str): Полный URL страницы на litres.ru или author.today.

        Returns:
            dict: Словарь с данными о книге (title, authors, series, etc.). 
                  Если произошла ошибка, содержит ключ 'error'.
        """
        url_lower = url.lower()
        try:
            if 'author.today' in url_lower:
                soup = self._get_rendered_soup(url)
                parsed_data = self.parse_authors_today(soup, url)
            else:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                response.encoding = 'utf-8'
                soup = BeautifulSoup(response.text, 'html.parser')
                if 'litres.ru' in url_lower:
                    parsed_data = self.parse_litres(soup, url)
                else:
                    parsed_data = self._empty_book_data(url)

            # Нормализуем рейтинг в единое поле (строка)
            if 'rating' in parsed_data:
                parsed_data['rating'] = self._to_str(parsed_data.get('rating', ''))
            return parsed_data
        except requests.exceptions.RequestException as e:
            msg = f"Невозможно подключиться к сайту {url}: {e}"
            self._safe_print(msg)
            parsed_data = self._empty_book_data(url)
            parsed_data['error'] = msg
            return parsed_data
        except Exception as e:
            msg = f"Ошибка при парсинге {url}: {e}"
            self._safe_print(msg)
            self._safe_print(traceback.format_exc())
            parsed_data = self._empty_book_data(url)
            parsed_data['error'] = msg
            return parsed_data

    def parse_litres(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """Парсит данные с litres.ru"""
        book_data = self._empty_book_data(url)

        # Получаем данные из JSON-LD
        json_ld_data = self._get_json_ld_data(soup)

        # Название
        book_data['title'] = json_ld_data.get('name') or self._get_text(soup, [
            'h1[itemprop="name"]',
            'h1.biblio_book_name',
            'h1.biblio-book-name',
            '.biblio_book_name',
            'h1'
        ])

        # Авторы
        if json_ld_data.get('authors'):
            book_data['authors'] = json_ld_data['authors']
        else:
            book_data['authors'] = self._get_authors(soup)

        # Тип книги
        book_data['type'] = json_ld_data.get(
            'type') or self._get_book_type(url)

        # Серия
        book_data['series'] = self._get_series(soup)

        # Номер в серии
        book_data['seriesNumber'] = self._get_series_number(
            soup, book_data['title'])

        # Аннотация
        book_data['annotation'] = json_ld_data.get(
            'description') or self._get_annotation(soup)

        # Обложка
        cover_url = json_ld_data.get('image') or self._get_image(soup, [
            'img[itemprop="image"]',
            '.biblio-book-cover img',
            '.biblio_book_cover img',
            '.art-item img',
            'img.biblio-book-cover',
            'img.cover-image'
        ])
        if cover_url:
            book_data['coverUrl'] = self._make_absolute_url(cover_url, url)

        # Рейтинг с сайта
        rating_data = self._get_rating(soup)
        book_data['rating'] = rating_data.get('stars', '')

        # Жанры
        book_data['genres'] = self._get_litres_genres(soup)

        # Чтец (для аудиокниг)
        if book_data['type'] == 'Audiobook':
            if json_ld_data.get('readers'):
                book_data['reader'] = json_ld_data['readers']
            else:
                book_data['reader'] = self._get_reader(soup)

        return book_data

    def parse_authors_today(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """
        Парсит данные о книге с сайта author.today.

        Особенность author.today в том, что:
        - JSON-LD (script[type=\"application/ld+json\"]) часто содержит «чистое»
          название без номера тома;
        - Визуальный заголовок на странице (h1 / .book-title) может содержать
          номер части/тома в формате «Название - 8».

        В этой реализации мы:
        1. Сначала читаем JSON-LD, чтобы получить базовые метаданные.
        2. Дополнительно читаем DOM-заголовок и, если он содержит номер части
           (цифру после дефиса), предпочитаем именно его, чтобы не терять номер.
        3. Обрезаем из заголовка только автора в формате «Название - Автор»,
           но не обрезаем номер части «Название - 8».
        """
        book_data = self._empty_book_data(url)

        json_ld_data = self._get_json_ld_data(soup)

        # Базовое название из JSON-LD (часто без номера тома)
        json_title = json_ld_data.get('name') or ''
        # Визуальный заголовок страницы (часто содержит номер тома: «... - 8»)
        dom_title = self._get_text(
            soup,
            [
                '.book-title',
                'h1[itemprop="name"]',
                'h1',
            ],
        )

        title = json_title or dom_title

        # Если в DOM-заголовке есть номер части в конце (после дефиса),
        # а JSON-LD его не содержит, используем именно DOM-версию.
        # Пример с https://author.today/audiobook/497189:
        #   json_title: "Первый среди равных"
        #   dom_title:  "Первый среди равных - 8"
        # В этом случае хотим сохранить « - 8».
        if dom_title:
            m_part = re.search(r'\d+\s*$', dom_title)
            if m_part:
                # В dom_title в конце есть цифра — берём его целиком.
                title = dom_title

        # Убираем автора из названия (формат "Название - Автор"),
        # но НЕ трогаем случаи, когда после дефиса только номер части.
        # Примеры:
        #   "Название - Автор"      -> "Название"
        #   "Название - 8"          -> "Название - 8"  (номер тома сохраняем)
        if title:
            m = re.match(r'^(.*?)\s*-\s*(.+)$', title)
            if m:
                left, right = m.group(1).strip(), m.group(2).strip()
                # Если правая часть содержит буквы (т.е. это автор),
                # обрезаем её. Если там только цифры — считаем это номером
                # части и оставляем как есть.
                if re.search(r'[A-Za-zА-Яа-я]', right):
                    title = left

        book_data['title'] = title

        # Авторы
        if json_ld_data.get('authors'):
            # JSON-LD иногда отдаёт авторов одной строкой через запятую.
            authors: List[str] = []
            for a in json_ld_data['authors']:
                if isinstance(a, str) and ',' in a:
                    authors.extend([p.strip() for p in a.split(',') if p.strip()])
                else:
                    s = str(a).strip()
                    if s:
                        authors.append(s)
            book_data['authors'] = authors[:5]
        else:
            book_data['authors'] = self._get_authors_from_authors_today(soup)

        # Тип книги
        book_data['type'] = json_ld_data.get(
            'type') or self._get_book_type(url)

        # Серия
        book_data['series'] = self._get_series(
            soup) or self._get_authors_today_series(soup)
        book_data['seriesNumber'] = self._get_series_number(
            soup, book_data['title'])

        # Аннотация
        book_data['annotation'] = json_ld_data.get('description') or self._get_text(soup, [
            '.annotation [itemprop="description"]',
            '.annotation',
            '[itemprop="description"]',
            '.work-description'
        ])

        # Обложка
        cover_url = json_ld_data.get('image') or self._get_meta_content(soup, [
            'meta[property="og:image"]',
            'meta[name="twitter:image"]'
        ]) or self._get_image(soup, [
            '.book-cover img',
            '.book-cover .cover-image img',
            'img.cover-image',
            'img[itemprop="image"]'
        ])
        if cover_url:
            book_data['coverUrl'] = self._make_absolute_url(cover_url, url)

        # Рейтинг с сайта
        # Для author.today сначала пытаемся получить рейтинг в формате "👍 число"
        authors_today_rating = self._get_authors_today_rating(soup)
        if authors_today_rating:
            book_data['rating'] = authors_today_rating
        else:
            book_data['rating'] = 'Рейтинг не найден'

        # Жанры — только теги книги, без навигационных жанров сайта
        book_data['genres'] = self._get_authors_today_genres(soup)

        # Чтец (для аудиокниг)
        if book_data['type'] == 'Audiobook':
            if json_ld_data.get('readers'):
                book_data['reader'] = json_ld_data['readers']
            else:
                book_data['reader'] = self._get_authors_today_readers(soup)

        return book_data

    def combine_book_data(self, site_data: Dict[str, Any], file_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Объединяет данные с сайта и данные из файла.

        Args:
            site_data (dict): Данные, полученные с сайта (результат parse_url)
            file_data (dict): Данные из входного файла (результат parse_line)

        Returns:
            dict: Объединенные данные со всеми полями
        """
        # Копируем данные с сайта
        combined = site_data.copy()

        # Добавляем данные из файла
        combined.update({
            'date': file_data.get('date', ''),
            'useful': file_data.get('useful', ''),
            'comment': file_data.get('comment', ''),
            # Явно раскладываем, чтобы downstream-код не путал источники.
            'rating_from_file': self._to_str(file_data.get('rating', '')),  # Личный рейтинг
            'rating_from_site': self._to_str(site_data.get('rating', '')),  # Рейтинг с сайта
        })

        # Удаляем неоднозначное поле rating
        combined.pop('rating', None)

        return combined

    def parse_and_combine(self, url: str, file_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Удобный метод для парсинга URL и объединения с данными из файла.

        Args:
            url (str): URL книги
            file_data (dict): Данные из входного файла

        Returns:
            dict: Объединенные данные
        """
        site_data = self.parse_url(url)
        return self.combine_book_data(site_data, file_data)

    def _empty_book_data(self, url: str) -> Dict[str, Any]:
        """Создает пустую структуру данных о книге"""
        return {
            'title': '',
            'authors': [],
            'series': '',
            'seriesNumber': '',
            'annotation': '',
            'coverUrl': '',
            'rating': '',
            'type': 'Book',
            'reader': [],
            'genres': [],
            'url': url,
            'date': '',
            'useful': '',
            'comment': '',
            'rating_from_site': '',  # Рейтинг с сайта
            'rating_from_file': '',  # Рейтинг из файла
            'error': None
        }

    def _get_json_ld_data(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Извлекает данные из JSON-LD"""
        result = {
            'name': '',
            'authors': [],
            'type': '',
            'description': '',
            'image': '',
            'readers': []
        }

        scripts = soup.find_all('script', type='application/ld+json')
        for script in scripts:
            try:
                data = json.loads(script.string)
                if not isinstance(data, dict):
                    continue

                # Проверяем тип
                if data.get('@type') in ['Book', 'Audiobook']:
                    result['type'] = 'Audiobook' if data['@type'] == 'Audiobook' else 'Book'

                    if data.get('name') and not result['name']:
                        result['name'] = data['name']

                    # Автор
                    if data.get('author'):
                        authors = []
                        if isinstance(data['author'], list):
                            for a in data['author']:
                                if isinstance(a, dict):
                                    authors.append(a.get('name', ''))
                                else:
                                    authors.append(str(a))
                        elif isinstance(data['author'], dict):
                            authors.append(data['author'].get('name', ''))
                        else:
                            # Если автор - строка, разделяем по запятым
                            author_str = str(data['author'])
                            split_authors = [
                                a.strip() for a in author_str.split(',') if a.strip()]
                            authors.extend(split_authors)
                        result['authors'] = [a for a in authors if a]

                    # Описание
                    if data.get('description') and not result['description']:
                        result['description'] = data['description']

                    # Изображение
                    if data.get('image') and not result['image']:
                        img = data['image']
                        if isinstance(img, str):
                            result['image'] = img
                        elif isinstance(img, dict):
                            result['image'] = img.get('url', '')
                        elif isinstance(img, list) and img:
                            result['image'] = img[0] if isinstance(
                                img[0], str) else img[0].get('url', '')

                    # Чтецы
                    if data['@type'] == 'Audiobook' and data.get('readBy'):
                        readers = []
                        if isinstance(data['readBy'], list):
                            for r in data['readBy']:
                                if isinstance(r, dict):
                                    readers.append(r.get('name', ''))
                                else:
                                    readers.append(str(r))
                        elif isinstance(data['readBy'], dict):
                            readers.append(data['readBy'].get('name', ''))
                        else:
                            readers.append(str(data['readBy']))
                        result['readers'] = [r for r in readers if r]

            except (json.JSONDecodeError, KeyError, AttributeError):
                continue

        return result

    def _get_text(self, soup: BeautifulSoup, selectors: List[str]) -> str:
        """Извлекает текст по селекторам"""
        for selector in selectors:
            try:
                element = soup.select_one(selector)
                if element:
                    text = element.get_text(strip=True)
                    if text:
                        return text
            except:
                continue
        return ''

    def _get_authors(self, soup: BeautifulSoup) -> List[str]:
        """Извлекает авторов"""
        authors = []

        # Через itemprop="author"
        author_elements = soup.find_all(attrs={'itemprop': 'author'})
        for el in author_elements:
            # Проверяем, что не из рекомендаций
            parent = el.find_parent(class_=re.compile(
                r'recommend|similar|related|slider|carousel'))
            if not parent:
                name = el.get('content') or el.get_text(strip=True)
                if name and len(name) < 100:
                    name = re.sub(r'[,\s]+$', '', name).strip()
                    # Разделяем авторов по запятым, если их несколько в одной строке
                    if ',' in name:
                        split_names = [n.strip()
                                       for n in name.split(',') if n.strip()]
                        for split_name in split_names:
                            if split_name and split_name not in authors:
                                authors.append(split_name)
                    elif name and name not in authors:
                        authors.append(name)

        if authors:
            return authors[:5]

        # Через ссылки на авторов
        main_content = soup.find('main') or soup.find(role='main') or soup.find(
            class_=re.compile(r'content|book-page|product-page')) or soup
        author_links = main_content.find_all('a', href=re.compile(r'/author/'))

        excluded_classes = re.compile(
            r'recommend|similar|related|slider|carousel')
        for link in author_links:
            parent = link.find_parent(class_=excluded_classes)
            if not parent:
                text = link.get_text(strip=True)
                if text and text not in authors and len(text) < 100:
                    authors.append(text)

        return authors[:5] if authors else []

    def _get_authors_from_authors_today(self, soup: BeautifulSoup) -> List[str]:
        """Извлекает авторов с author.today"""
        authors = []
        container = soup.find(class_='book-authors')
        if container:
            for link in container.find_all('a'):
                text = link.get_text(strip=True)
                if text and text not in authors and len(text) < 100:
                    authors.append(text)

        if not authors:
            return self._get_authors(soup)

        return authors[:5]

    def _get_series(self, soup: BeautifulSoup) -> str:
        """Извлекает название серии"""
        series = self._get_text(soup, [
            'a[href*="/series/"]',
            '.biblio_book_series a',
            '.biblio-book-series a',
            '.series a',
            '.book-series a'
        ])

        # Убираем кавычки
        if series:
            # Удаляем различные типы кавычек: прямые, типографские, фигурные
            # Используем Unicode escape sequences для специальных символов
            quotes_pattern = r'["\'\u00AB\u00BB\u201E\u201C\u201D\u2018\u2019\u201A\u201B\u2039\u203A]'
            series = re.sub(quotes_pattern, '', series).strip()

        return series

    def _get_authors_today_series(self, soup: BeautifulSoup) -> str:
        """Извлекает серию с author.today"""
        labels = soup.find_all(class_='text-muted')
        for label in labels:
            text = label.get_text(strip=True).lower()
            if 'цикл' in text:
                parent = label.find_parent()
                if parent:
                    link = parent.find('a')
                    if link:
                        series_name = link.get_text(strip=True)
                        # Удаляем различные типы кавычек (используем Unicode escape sequences)
                        quotes_pattern = r'["\'\u00AB\u00BB\u201E\u201C\u201D\u2018\u2019\u201A\u201B\u2039\u203A]'
                        series_name = re.sub(
                            quotes_pattern, '', series_name).strip()
                        series_name = re.sub(
                            r'\s*\(аудио\)\s*$', '', series_name, flags=re.IGNORECASE).strip()
                        return series_name
        return ''

    def _get_series_number(self, soup: BeautifulSoup, title: str) -> str:
        """Извлекает номер книги в серии"""
        # Ищем в тексте страницы
        page_text = soup.get_text()
        match = re.search(
            r'(\d+)\s*(?:книга|том|часть)?\s*из\s*\d+', page_text, re.IGNORECASE)
        if match:
            return match.group(1)

        # Ищем в селекторах серии
        series_elements = soup.find_all(['a'], href=re.compile(r'/series/'))
        for el in series_elements:
            text = el.get_text()
            match = re.search(
                r'(\d+)\s*(?:книга|том|часть)?\s*из\s*\d+', text, re.IGNORECASE)
            if match:
                return match.group(1)

        # Ищем в конце названия
        if title:
            match = re.search(r'\s+(\d+)$', title)
            if match and len(match.group(1)) <= 3:
                return match.group(1)

            # Ищем в начале или середине
            match = re.search(
                r'(?:книга|том|часть|#|№)\s*(\d+)', title, re.IGNORECASE)
            if match:
                return match.group(1)

        return ''

    def _get_annotation(self, soup: BeautifulSoup) -> str:
        """Извлекает аннотацию"""
        return self._get_text(soup, [
            '[itemprop="description"]',
            '.biblio_book_descr',
            '.biblio-book-description',
            '.annotation',
            '.description',
            '#annotation',
            '.book-annotation'
        ])

    def _get_rating(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Извлекает рейтинг"""
        rating_value = 0

        selectors = [
            '[itemprop="ratingValue"]',
            '.rating-value',
            '.book-rating',
            '.rating',
            '[data-rating]'
        ]

        for selector in selectors:
            try:
                element = soup.select_one(selector)
                if element:
                    # Из атрибута
                    attr_value = element.get(
                        'data-rating') or element.get('data-value') or element.get('content')
                    if attr_value:
                        try:
                            rating_value = float(attr_value)
                            break
                        except:
                            pass

                    # Из текста
                    text = element.get_text(strip=True)
                    if text:
                        match = re.search(r'(\d+[.,]?\d*)', text)
                        if match:
                            try:
                                rating_value = float(
                                    match.group(1).replace(',', '.'))
                                break
                            except:
                                pass
            except:
                continue

        # Конвертируем в звездочки
        stars = ''
        if rating_value > 0:
            stars_count = min(int(round(rating_value)), 5)
            stars = '⭐️' * stars_count

        return {'value': rating_value, 'stars': stars}

    def _get_authors_today_rating(self, soup: BeautifulSoup) -> str:
        """
        Извлекает основной рейтинг книги (количество лайков) с author.today по строгому селектору, заданному пользователем.
        Возвращает строку вида "+<number>" (без смайла) либо пустую строку, если не найдено.
        """

        try:

            like = soup.select_one(AUTHOR_TODAY_LIKE_SELECTOR)
            if like:
                text = like.get_text(strip=True)
                like_count = int(re.sub(r'[^\d]', '', text))
                return f"+{like_count}"
            else:
                return ''
        except Exception as e:
            self._safe_print(f"author.today rating parse error: {e}")
            return ''

    def _get_book_type(self, url: str) -> str:
        """Определяет тип книги по URL. По умолчанию возвращает 'Book', если шаблон URL не распознан."""
        url_lower = url.lower()
        if '/audiobook/' in url_lower:
            return 'Audiobook'
        elif '/book/' in url_lower:
            return 'Book'
        # Дефолт: неизвестный путь классифицируем как обычную книгу
        return 'Book'

    def _get_reader(self, soup: BeautifulSoup) -> List[str]:
        """Извлекает чтеца"""
        readers = []

        selectors = [
            '[itemprop="readBy"]',
            '.reader',
            '.narrator',
            '.audiobook-reader'
        ]

        for selector in selectors:
            try:
                elements = soup.select(selector)
                for el in elements:
                    parent = el.find_parent(
                        class_=re.compile(r'recommend|similar|related'))
                    if not parent:
                        name = el.get('content') or el.get_text(strip=True)
                        if name and name not in readers and len(name) < 100:
                            readers.append(name)
                if readers:
                    break
            except:
                continue

        # Через ссылки
        main_content = soup.find('main') or soup.find(role='main') or soup
        reader_links = main_content.find_all(
            'a', href=re.compile(r'/reader/|/narrator/'))

        for link in reader_links:
            parent = link.find_parent(
                class_=re.compile(r'recommend|similar|related'))
            if not parent:
                text = link.get_text(strip=True)
                if text and text not in readers and len(text) < 100:
                    readers.append(text)

        return readers[:5]

    def _get_authors_today_readers(self, soup: BeautifulSoup) -> List[str]:
        """Извлекает чтецов с author.today"""
        readers = []
        labels = soup.find_all(class_='text-muted')
        for label in labels:
            text = label.get_text(strip=True).lower()
            if 'чтец' in text:
                parent = label.find_parent()
                if parent:
                    for link in parent.find_all('a'):
                        name = link.get_text(strip=True)
                        if name and name not in readers:
                            readers.append(name)
        return readers[:5]

    def _get_litres_genres(self, soup: BeautifulSoup) -> List[str]:
        """Извлекает жанры/теги книги из блока «Жанры и теги» на litres.ru."""
        for heading in soup.find_all(['h4', 'h3', 'h2']):
            if heading.get_text(strip=True) != 'Жанры и теги':
                continue

            parent = heading.find_parent()
            if not parent:
                continue

            genres = []
            for link in parent.find_all('a'):
                text = link.get_text(strip=True)
                if text and text not in genres:
                    genres.append(text)
            if genres:
                return genres

        return []

    def _get_authors_today_genres(self, soup: BeautifulSoup) -> List[str]:
        """Извлекает пользовательские теги книги с author.today."""
        genres = []
        tag_container = soup.select_one('.tags')
        if not tag_container:
            return genres

        for link in tag_container.select('a[href*="/tag/"]'):
            text = link.get_text(strip=True)
            if text and text not in genres:
                genres.append(text)
        return genres

    def _get_image(self, soup: BeautifulSoup, selectors: List[str]) -> str:
        """Извлекает URL изображения"""
        for selector in selectors:
            try:
                img = soup.select_one(selector)
                if img:
                    url = img.get('src') or img.get(
                        'data-src') or img.get('data-lazy-src')
                    if url:
                        return url
            except:
                continue
        return ''

    def _get_meta_content(self, soup: BeautifulSoup, selectors: List[str]) -> str:
        """Извлекает содержимое meta тегов"""
        for selector in selectors:
            try:
                element = soup.select_one(selector)
                if element:
                    content = element.get('content') or element.get('href')
                    if content:
                        return content
            except:
                continue
        return ''

    def _make_absolute_url(self, url: str, base_url: str) -> str:
        """Преобразует относительный URL в абсолютный"""
        if not url:
            return ''

        url = url.strip()
        if not url:
            return ''

        try:
            if url.startswith('//'):
                parsed = urlparse(base_url)
                return f"{parsed.scheme}:{url}"
            elif url.startswith('/'):
                parsed = urlparse(base_url)
                return f"{parsed.scheme}://{parsed.netloc}{url}"
            elif url.startswith(('http://', 'https://')):
                return url
            else:
                return urljoin(base_url, url)
        except:
            return url

    @staticmethod
    def _to_str(value: Any) -> str:
        """Безопасно преобразует значение в строку (для rating и др.)."""
        if value is None:
            return ''
        return str(value).strip()
