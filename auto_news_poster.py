import requests
import telegram
from telegram import ParseMode
import os
import random
from dotenv import load_dotenv
import sys
import json
from datetime import datetime, timedelta
import time
import logging
import asyncio
import google.generativeai as genai # Для Gemini

# --- Загрузка конфигурации ---
try:
    load_dotenv()
    # Используем существующие переменные для Facebook и Telegram
    FACEBOOK_PAGE_ID = os.getenv('FACEBOOK_PAGE_ID')
    FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv('FACEBOOK_PAGE_ACCESS_TOKEN')
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
    PHOTO_FOLDER_PATH = os.getenv('PHOTO_FOLDER_PATH')
    USED_PHOTOS_FILE = os.getenv('USED_PHOTOS_FILE')

    # Переменные для News API и Gemini
    NEWS_API_KEY = os.getenv("NEWS_API_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

    required_vars_dict = {
        "FACEBOOK_PAGE_ID": FACEBOOK_PAGE_ID,
        "FACEBOOK_PAGE_ACCESS_TOKEN": FACEBOOK_PAGE_ACCESS_TOKEN,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "PHOTO_FOLDER_PATH": PHOTO_FOLDER_PATH,
        "USED_PHOTOS_FILE": USED_PHOTOS_FILE,
        "NEWS_API_KEY": NEWS_API_KEY,
        "GEMINI_API_KEY": GEMINI_API_KEY,
    }
    missing_vars = [key for key, value in required_vars_dict.items() if not value]
    if missing_vars:
        raise ValueError(f"Следующие обязательные переменные окружения не установлены в .env файле: {', '.join(missing_vars)}")

except ValueError as e:
    print(f"Ошибка конфигурации: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Произошла ошибка при загрузке .env: {e}")
    sys.exit(1)

# Файл для хранения URL обработанных новостей
PROCESSED_NEWS_FILE = "processed_news.json"

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Настройки Facebook Graph API (из main.py) ---
GRAPH_API_VERSION = "v19.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# --- Функции select_unique_photo и mark_photo_as_used (скопированы из main.py) ---
def select_unique_photo(photo_dir, used_photos_file):
    used_photos = set()
    try:
        if not os.path.exists(used_photos_file):
            with open(used_photos_file, 'w', encoding='utf-8') as f: pass
            logging.info(f"Создан файл для отслеживания фото: {used_photos_file}")
        with open(used_photos_file, 'r', encoding='utf-8') as f:
            used_photos = set(line.strip() for line in f if line.strip())
        all_photos = [f for f in os.listdir(photo_dir)
                      if os.path.isfile(os.path.join(photo_dir, f)) and f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if not all_photos:
             logging.error(f"Ошибка: Папка с фото ({photo_dir}) пуста или не содержит поддерживаемых форматов.")
             return None
        available_photos = [f for f in all_photos if f not in used_photos]
        if not available_photos:
            logging.warning("Предупреждение: В папке не осталось неиспользованных фотографий! Сбрасываю список.")
            open(used_photos_file, 'w').close()
            available_photos = all_photos
            if not available_photos:
                logging.error(f"Ошибка: Папка с фото ({photo_dir}) все еще пуста после сброса.")
                return None
        selected_photo_name = random.choice(available_photos)
        return os.path.join(photo_dir, selected_photo_name)
    except FileNotFoundError:
        logging.error(f"Ошибка: Папка с фотографиями не найдена: {photo_dir}")
        return None
    except Exception as e:
        logging.error(f"Ошибка при выборе фото: {e}")
        return None

def mark_photo_as_used(photo_path, used_photos_file):
    try:
        photo_name = os.path.basename(photo_path)
        with open(used_photos_file, 'a', encoding='utf-8') as f:
            f.write(photo_name + '\n')
        logging.info(f"Фото {photo_name} помечено как использованное.")
    except Exception as e:
        logging.error(f"Ошибка при пометке фото {photo_name} как использованного: {e}")

# --- Функции post_to_facebook_with_photo и post_to_telegram_with_photo (адаптированы) ---
def post_to_facebook_with_photo(page_id, access_token, message, image_path):
    # Используем photos endpoint для постинга с локальной картинкой
    post_url = f"{GRAPH_URL}/{page_id}/photos"
    payload = {'access_token': access_token, 'caption': message}
    files = None
    logging.info(f"Публикация в Facebook (Page ID: {page_id})...")
    try:
        files = {'source': open(image_path, 'rb')}
        response = requests.post(post_url, data=payload, files=files, timeout=60)
        response.raise_for_status()
        result = response.json()
        logging.info(f"Facebook: Успешно опубликовано! Post ID: {result.get('post_id', 'N/A')}")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Facebook: Ошибка публикации: {e}")
        if e.response is not None: logging.error(f"FB Ответ: {e.response.text}")
        return False
    except FileNotFoundError:
         logging.error(f"Facebook: Ошибка - Файл фото не найден: {image_path}")
         return False
    except Exception as e:
        logging.error(f"Facebook: Неожиданная ошибка: {e}")
        return False
    finally:
        if files and files.get('source'):
            files['source'].close()

async def post_to_telegram_with_photo(bot_token, chat_id, message, image_path):
    logging.info(f"Публикация в Telegram (Chat ID: {chat_id})...")
    photo_file_handle = None
    try:
        bot = telegram.Bot(token=bot_token)
        photo_file_handle = open(image_path, 'rb')
        # Telegram caption limit is 1024 characters for photos
        caption = message[:1024]
        if len(message) > 1024:
            logging.warning("Telegram: Внимание, текст был обрезан до 1024 символов для подписи.")
        response = await bot.send_photo(chat_id=chat_id, photo=photo_file_handle, caption=caption, parse_mode=ParseMode.HTML)
        if response and hasattr(response, 'message_id'):
             logging.info(f"Telegram: Успешно опубликовано! Message ID: {response.message_id}")
             return True
        else:
             logging.error(f"Telegram: Ошибка - получен некорректный ответ от API: {response}")
             return False
    except telegram.error.BadRequest as e:
         if "can't parse entities" in str(e).lower():
              logging.warning("Telegram: Ошибка парсинга HTML, пробую отправить как простой текст...")
              try:
                   if photo_file_handle: photo_file_handle.seek(0)
                   response = await bot.send_photo(chat_id=chat_id, photo=photo_file_handle, caption=caption)
                   if response and hasattr(response, 'message_id'):
                        logging.info(f"Telegram: Успешно опубликовано (как простой текст)! Message ID: {response.message_id}")
                        return True
                   else:
                        logging.error(f"Telegram: Ошибка при повторной отправке - получен некорректный ответ от API: {response}")
                        return False
              except Exception as inner_e:
                   logging.error(f"Telegram: Ошибка при повторной отправке: {inner_e}")
                   return False
         else:
              logging.error(f"Telegram: Ошибка публикации (BadRequest): {e}")
              return False
    except telegram.error.TelegramError as e:
        logging.error(f"Telegram: Ошибка публикации ({type(e).__name__}): {e}")
        return False
    except FileNotFoundError:
         logging.error(f"Telegram: Ошибка - Файл фото не найден: {image_path}")
         return False
    except Exception as e:
        logging.error(f"Неожиданная ошибка при отправке в Telegram: {e}")
        return False
    finally:
         if photo_file_handle:
              photo_file_handle.close()

# --- Функции для работы с файлом обработанных новостей (из примера пользователя) ---
def load_processed_news():
    """Загружает список URL уже опубликованных новостей."""
    try:
        with open(PROCESSED_NEWS_FILE, 'r') as f:
            # Загружаем как список, возвращаем как множество для быстрой проверки
            return set(json.load(f))
    except FileNotFoundError:
        return set()
    except json.JSONDecodeError:
        logging.error(f"Ошибка чтения файла {PROCESSED_NEWS_FILE}. Создаем пустой список.")
        return set()

def save_processed_news(processed_set):
    """Сохраняет обновленный список URL опубликованных новостей."""
    try:
        with open(PROCESSED_NEWS_FILE, 'w') as f:
            # Сохраняем как список
            json.dump(list(processed_set), f, indent=4)
    except IOError as e:
        logging.error(f"Не удалось записать в файл {PROCESSED_NEWS_FILE}: {e}")

# --- Функция получения новостей с News API (из примера пользователя) ---
def get_crypto_news():
    """Получает новости с News API."""
    # Можно изменить 'q' на более специфичные запросы или использовать домен
    # Например: 'bitcoin OR ethereum OR cryptocurrency'
    # Используем endpoint 'everything' для большей гибкости
    # Добавляем фильтр по дате (последние 2 дня)
    from_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%S')
    url = f"https://newsapi.org/v2/everything?q=cryptocurrency&sortBy=publishedAt&language=en&from={from_date}&apiKey={NEWS_API_KEY}"
    # Для русских новостей language=ru, но их может быть мало по крипте
    # url_ru = f"https://newsapi.org/v2/everything?q=криптовалюта&sortBy=publishedAt&language=ru&apiKey={NEWS_API_KEY}"

    articles = []
    try:
        logging.info(f"Запрос новостей с News API: {url}")
        response = requests.get(url)
        response.raise_for_status() # Проверка на HTTP ошибки
        data = response.json()
        if data.get("status") == "ok":
            articles = data.get("articles", [])
            logging.info(f"Получено {len(articles)} новостей с News API.")
        else:
            logging.error(f"Ошибка News API: {data.get('message')}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка сети при запросе к News API: {e}")
    except Exception as e:
        logging.error(f"Неожиданная ошибка при получении новостей: {e}")
    return articles

# --- Функция перевода с помощью Google Gemini (из примера пользователя) ---
def translate_to_russian_gemini(text_to_translate):
    """Переводит текст на русский с помощью Google Gemini."""
    if not GEMINI_API_KEY:
        logging.error("Ключ Gemini API не найден.")
        return None
    if not text_to_translate:
        return "" # Возвращаем пустую строку, если текст пустой

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash') # Или другая подходящая модель

        prompt = f"Переведи следующий текст на русский язык. Выведи только перевод, без лишних фраз типа 'Вот перевод:'.\n\nТекст:\n{text_to_translate}"

        response = model.generate_content(prompt)
        translated_text = response.text.strip()
        logging.info("Текст успешно переведен Gemini.")
        return translated_text
    except Exception as e:
        logging.error(f"Ошибка при переводе через Gemini: {e}")
        return None # Возвращаем None в случае ошибки

# --- Основная логика обработки новостей (адаптирована) ---
async def process_news():
    """Основной цикл обработки новостей."""
    logging.info("Запуск цикла обработки новостей...")
    processed_urls = load_processed_news()
    logging.info(f"Загружено {len(processed_urls)} URL обработанных новостей.")

    articles = get_crypto_news()
    if not articles:
        logging.info("Новых статей не найдено.")
        return

    new_posts_count = 0
    # Фильтруем новости по длине и обрабатываем с самых старых к новым
    suitable_articles = []
    for article in reversed(articles):
        title = article.get('title', '')
        description = article.get('description', article.get('content', ''))
        full_text = f"{title} {description}" # Объединяем для оценки длины
        word_count = len(full_text.split())

        if word_count >= 300: # Проверяем минимальную длину (500 слов)
            suitable_articles.append(article)
            # Можно добавить сортировку suitable_articles по дате, если NewsAPI не гарантирует порядок

    if not suitable_articles:
        logging.info("Не найдено новостей подходящей длины (>= 500 слов). Завершение задачи.")
        return

    logging.info(f"Найдено {len(suitable_articles)} новостей подходящей длины.")

    # Обрабатываем подходящие статьи
    for article in suitable_articles:
        url = article.get('url')
        if not url or url in processed_urls:
            continue # Пропускаем, если нет URL или уже обработано

        title = article.get('title', '')
        description = article.get('description', article.get('content', '')) # Описание или контент
        image_url = article.get('urlToImage')
        source_name = article.get('source', {}).get('name', 'Неизвестный источник')

        # Очистка описания от лишних символов типа "[+1234 chars]"
        if description and '[+' in description:
            description = description.split('[+')[0].strip()

        # --- Перевод ---
        # Простая проверка: если API вернул язык 'en', переводим
        # В реальном приложении можно добавить более сложную детекцию языка
        language = article.get('language', 'en') # NewsAPI не всегда возвращает язык
        translated_title = title
        translated_description = description

        # Считаем, что переводить надо, если язык не русский
        if language != 'ru':
             logging.info(f"Требуется перевод для: {title}")
             # Переводим заголовок
             tt = translate_to_russian_gemini(title)
             if tt: translated_title = tt
             else: logging.warning("Не удалось перевести заголовок, используется оригинал.")

             # Переводим описание
             td = translate_to_russian_gemini(description)
             if td: translated_description = td
             else: logging.warning("Не удалось перевести описание, используется оригинал.")
        else:
            logging.info(f"Новость '{title}' уже на русском.")


        # --- Формирование поста ---
        # Используем HTML для форматирования в Telegram
        post_text_tg = f"<b>{translated_title}</b>\n\n"
        if translated_description:
            post_text_tg += f"{translated_description}\n\n"
        post_text_tg += f"Источник: <a href='{url}'>{source_name}</a>"

        # Текст для Facebook (без HTML)
        post_text_fb = f"{translated_title}\n\n"
        if translated_description:
            post_text_fb += f"{translated_description}\n\n"
        post_text_fb += f"Источник: {url}"

        # Ограничение длины для Telegram caption (1024) и Facebook
        # В Telegram caption лимит 1024, для текста сообщения больше
        # Если есть картинка, текст идет в caption
        # Если нет картинки, текст идет в message (лимит 4096)
        # Для простоты пока ограничимся 1024 для обоих случаев с фото
        # Если нет фото, можно использовать полный текст для Telegram message
        if image_url:
             post_text_tg = post_text_tg[:1020] + "..." if len(post_text_tg) > 1024 else post_text_tg
        # Facebook имеет бОльшие лимиты, но тоже стоит ограничить
        post_text_fb = post_text_fb[:4000] + "..." if len(post_text_fb) > 4000 else post_text_fb


        # --- Публикация ---
        logging.info(f"Публикуем новость: {title}")

        # Выбираем фото из локальной папки
        selected_image = select_unique_photo(PHOTO_FOLDER_PATH, USED_PHOTOS_FILE)
        if not selected_image:
            logging.warning("Не удалось выбрать фото для поста. Публикация без фото.")
            # Если нет фото, нужно адаптировать функции постинга для текста без фото
            # Пока оставим как есть, функции требуют image_path
            # В реальном приложении нужно добавить логику постинга только текста
            telegram_success = False
            facebook_success = False
        else:
            logging.info(f"Выбрано фото: {os.path.basename(selected_image)}")
            # Сначала пробуем Telegram
            telegram_success = await post_to_telegram_with_photo(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, post_text_tg, selected_image)
            time.sleep(2) # Небольшая пауза между постами в разные сети

            # Затем Facebook (если Telegram удался, или по логике приложения)
            # Убедитесь, что токен Facebook действителен!
            facebook_success = False
            if FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN:
                 facebook_success = post_to_facebook_with_photo(FACEBOOK_PAGE_ID, FACEBOOK_PAGE_ACCESS_TOKEN, post_text_fb, selected_image)
            else:
                logging.warning("Данные для Facebook не настроены, пропускаем публикацию.")


        # --- Обновление статуса ---
        # Считаем успех, если опубликовано хотя бы в одну сеть ИЛИ если не было фото
        if (telegram_success or facebook_success) or not selected_image:
            processed_urls.add(url)
            save_processed_news(processed_urls) # Сохраняем после каждого успешного поста
            new_posts_count += 1
            logging.info(f"Новость {url} успешно обработана и опубликована (или пропущена из-за отсутствия фото).")
        else:
            logging.error(f"Не удалось опубликовать новость {url} ни в одну сеть.")

        time.sleep(10) # Пауза между обработкой новостей во избежание rate limit

    logging.info(f"Цикл обработки завершен. Опубликовано {new_posts_count} новых новостей.")


# --- Запуск скрипта ---
if __name__ == "__main__":
    logging.info("--- ЗАПУЩЕН СКРИПТ АВТОМАТИЧЕСКОЙ ПУБЛИКАЦИИ НОВОСТЕЙ ---")
    # Проверяем наличие ключей перед запуском
    if not NEWS_API_KEY or not GEMINI_API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.critical("Один или несколько обязательных API ключей/ID не найдены в .env! Проверьте файл .env.")
        sys.exit(1) # Выходим, если ключи не настроены
    else:
        try:
            asyncio.run(process_news())
            logging.info("Скрипт завершил выполнение.")
            sys.exit(0)
        except KeyboardInterrupt:
            logging.info("\nСкрипт остановлен пользователем.")
            sys.exit(1)
        except Exception as e:
            logging.critical(f"\nНепредвиденная ошибка на верхнем уровне: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
