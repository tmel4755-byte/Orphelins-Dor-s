import telebot
import time
from telebot import types
from telebot.apihelper import ApiTelegramException
import json
import os
import logging
from datetime import datetime, timedelta

# --- КОНСТАНТЫ И ХРАНИЛИЩА ---
# Хранение корзин и заказов
CARTS = {}
ORDERS = {}         # order_id → { "user_id": ..., "items": [...], "status": "new", ... }
NEXT_ORDER_ID = 1
ORDERS_FILE = "orders.json"

# Хранение тикетов поддержки
# ticket_id -> { "user_id": ..., "username": ..., "status": "new"|"in_work"|"closed", "admin_msg_id": ..., "client_msg_id": ... }
SUPPORT_TICKETS = {}
NEXT_TICKET_ID = 1
SUPPORT_COOLDOWN_SECONDS = 300  # 5 минут

# ID группы для уведомлений о заказах
ADMIN_GROUP_ID = "-4975322862"  # Замените на ID вашей группы
SUPPORT_GROUP_ID = "-5095562342"  # ← ЗАМЕНИТЕ НА РЕАЛЬНЫЙ ID ВАШЕЙ ГРУППЫ ПОДДЕРЖКИ
# ID администраторов (добавьте свой ID)
ADMIN_IDS = [1144206940, 6539363874] #6539363874
# Хранилище состояний пользователей
user_data = {}
last_bot_msg = {}


PRODUCTS_FILE = "products.json"
PRODUCTS = {"welcome": None, "shoes": [], "clothes": []}

FAQ_ANSWERS = [
    "1. Выберите товар и размер\n2. Нажмите «➕ В корзину» или «🛒 Заказать»\n3. Перейдите в корзину и нажмите «📦 Оформить заказ»\n4. Ожидайте сообщения от менеджера в течение 15 минут",
    "Оплата производится **100% предоплатой**:\n• Перевод на СБП (Систему быстрых платежей)\n• QR-код\n\nПосле оплаты мы отправляем товар в тот же день.",
    "г. Новосибирск, ул. Крылова, д. 1\n\nСамовывоз возможен по предварительной договорённости.",
    "Возврат возможен **в течение 14 дней**, если:\n• Товар не был в носке\n• Сохранены ярлыки и упаковка\n\nОбратитесь к менеджеру через бота.",
]

_product_cache = {}


logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# Вставьте ваш токен бота
BOT_TOKEN = "8556338852:AAGXRSJrg87P8BoRsmArzc3bVXWAT1d6dqo"
bot = telebot.TeleBot(BOT_TOKEN)

# --- УТИЛИТЫ ---

def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_user_info(user):
    username = f"@{user.username}" if user.username else "Нет username"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Нет имени"
    return username, full_name

def get_next_product_id(category):
    max_id = 0
    for cat in PRODUCTS:
        if isinstance(PRODUCTS[cat], list):
            for product in PRODUCTS[cat]:
                if product.get('id', 0) > max_id:
                    max_id = product['id']
    return max_id + 1

def save_products():
    with open(PRODUCTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(PRODUCTS, f, ensure_ascii=False, indent=4)

def load_products():
    global PRODUCTS
    if os.path.exists(PRODUCTS_FILE):
        with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        # Восстанавливаем структуру по умолчанию
        PRODUCTS = {
            "welcome": raw.get("welcome"),
            "shoes": raw.get("shoes", []),
            "clothes": raw.get("clothes", [])
        }
        # Логируем содержимое
        logger.debug(f"Загружено товаров: обувь={len(PRODUCTS['shoes'])}, одежда={len(PRODUCTS['clothes'])}")
    else:
        save_products()

def save_orders():
    global NEXT_ORDER_ID
    data = {"next_order_id": NEXT_ORDER_ID, "orders": ORDERS}
    with open(ORDERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_orders():
    global NEXT_ORDER_ID, ORDERS
    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            NEXT_ORDER_ID = data.get("next_order_id", 1)
            ORDERS = data.get("orders", {})
            # Конвертируем ключи обратно в int, если они были строками
            ORDERS = {int(k): v for k, v in ORDERS.items()}
    else:
        save_orders()

def find_product_by_id(product_id):
    if product_id in _product_cache:
        return _product_cache[product_id]
    
    for category in PRODUCTS:
        if isinstance(PRODUCTS[category], list):
            for product in PRODUCTS[category]:
                if product.get('id') == product_id:
                    _product_cache[product_id] = product
                    return product
    return None

# --- МЕНЮ ---

def get_reply_main_menu():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    mk.add(
        types.KeyboardButton("👟 Смотреть обувь"),
        types.KeyboardButton("👕 Смотреть одежду")
    )
    mk.add(
        types.KeyboardButton("🛒 Корзина"),
        types.KeyboardButton("🔥 Sale до -50%")
    )
    mk.add(
        types.KeyboardButton("🆘 Поддержка")
    )
    return mk

def get_admin_reply_menu():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    mk.add("➕ Добавить товар", "✏️ Редактировать")
    mk.add("🗑 Удалить товар", "🖼 Приветствие")
    mk.add("📊 Статистика", "🚚 Заказы")
    mk.add("◀️ Главное меню")
    return mk

def get_admin_add_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ Обувь", callback_data="admin_add_shoes"),
        types.InlineKeyboardButton("➕ Одежда", callback_data="admin_add_clothes")
    )
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin_panel"))
    return markup


def get_admin_category_menu():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    mk.add("👟 Обувь", "👕 Одежда")
    mk.add("◀️ Назад")
    return mk

def get_admin_edit_products_reply_menu(category: str):
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    products = PRODUCTS.get(category, [])
    for product in products:
        if isinstance(product, dict) and 'name' in product:
            mk.add(f"{product['name']} - {product['price']} ₽")
    mk.add("◀️ Назад")
    return mk


@bot.message_handler(func=lambda message: message.text == "✏️ Редактировать")
def admin_edit_select_reply(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(
        message.chat.id,
        "Выберите категорию:",
        reply_markup=get_admin_category_menu()
    )



@bot.message_handler(func=lambda message: message.text == "👟 Обувь")
def admin_edit_shoes_reply(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(
        message.chat.id,
        "Выберите товар для редактирования:",
        reply_markup=get_admin_edit_products_reply_menu("shoes")
    )

@bot.message_handler(func=lambda message: " - " in message.text and "₽" in message.text)
def admin_edit_product_by_name(message):
    if not is_admin(message.from_user.id):
        return

    text = message.text
    name = text.split(" - ")[0].strip()

    product = None
    for category in ["shoes", "clothes"]:
        for p in PRODUCTS.get(category, []):
            if isinstance(p, dict) and p.get('name') == name:
                product = p
                break
        if product:
            break

    if not product:
        bot.send_message(message.chat.id, "❌ Товар не найден.")
        return

    product_id = product['id']
    bot.send_message(
        message.chat.id,
        f"✏️ Что вы хотите изменить для *{product['name']}*?",
        parse_mode="Markdown",
        reply_markup=get_admin_product_actions_reply_menu(product_id)
    )

def get_admin_product_actions_reply_menu(product_id):
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    mk.add("📝 Название", "💰 Цену")
    mk.add("📏 Размеры", "🖼 Фото")
    mk.add("📦 Наличие", "◀️ Назад")
    return mk


@bot.message_handler(func=lambda message: message.text == "📝 Название")
def admin_change_name_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    user_data[user_id] = {'waiting_for': 'name_edit'}
    bot.send_message(message.chat.id, "Введите новое название товара:")



@bot.message_handler(func=lambda message: user_data.get(message.from_user.id, {}).get('waiting_for') == 'name_edit')
def admin_edit_name_handler(message):
    if not is_admin(message.from_user.id):
        return

    new_name = message.text.strip()
    # Получаем product_id из user_data
    product_id = user_data[message.from_user.id].get('editing_product_id')
    if not product_id:
        bot.send_message(message.chat.id, "❌ Ошибка: не выбран товар.")
        return

    updated = False
    for category in ["shoes", "clothes"]:
        for product in PRODUCTS.get(category, []):
            if isinstance(product, dict) and product.get('id') == product_id:
                product['name'] = new_name
                updated = True
                break
        if updated:
            save_products()
            bot.send_message(message.chat.id, "✅ Название обновлено!", reply_markup=get_admin_reply_menu())
            break

    user_data[message.from_user.id].pop('waiting_for', None)
    user_data[message.from_user.id].pop('editing_product_id', None)

@bot.callback_query_handler(func=lambda c: c.data.startswith("order_delete_"))
def admin_order_delete(call):
    if not is_admin(call.from_user.id):
        return
    
    try:
        order_id = int(call.data.split("_")[2])
        if order_id in ORDERS:
            del ORDERS[order_id]
            save_orders()
            bot.answer_callback_query(call.id, "Заказ удалён!")
            # Возвращаемся в список
            admin_orders_list(call)
        else:
            bot.answer_callback_query(call.id, "Заказ не найден")
    except Exception as e:
        bot.answer_callback_query(call.id, "Ошибка удаления")

def get_orders_list_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
   
    sorted_orders = sorted(
        ORDERS.items(),
        key=lambda x: (x[1]['status'] != 'new', x[1]['created_at']),
        reverse=True
    )
   
    if not sorted_orders:
        markup.add(types.InlineKeyboardButton("Нет заказов", callback_data="noop"))
    else:
        for order_id, order in sorted_orders:
            status_emoji = {
                'new': '🆕',
                'in_processing': '🔄',
                'sent': '🚚',
                'completed': '✅'
            }.get(order['status'], '❓')
           
            items_count = len(order['items'])
            if items_count == 1:
                item_info = f"{order['items'][0]['name']} ({order['items'][0]['size']})"
            else:
                item_info = f"{items_count} товаров"
           
            client_name = order['full_name'][:15] + "..." if len(order['full_name']) > 18 else order['full_name']
            item_info_short = item_info[:25] + "..." if len(item_info) > 28 else item_info
           
            button_text = f"{status_emoji} #{order_id} | {item_info_short} | {client_name}"
           
            markup.add(types.InlineKeyboardButton(
                button_text,
                callback_data=f"order_view_{order_id}"
            ))
   
    markup.add(types.InlineKeyboardButton("◀️ Назад в админ-панель", callback_data="admin_panel"))
    return markup


def get_admin_edit_menu(category):
    markup = types.InlineKeyboardMarkup(row_width=1)
    products = PRODUCTS.get(category, [])
    if not isinstance(products, list):
        products = []
    for product in products:
        if isinstance(product, dict) and 'id' in product:
            markup.add(types.InlineKeyboardButton(
                f"{product['name']} - {product['price']} ₽",
                callback_data=f"admin_edit_prod_{product['id']}"
            ))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin_panel"))
    return markup

def get_admin_product_actions(product_id):
    product = find_product_by_id(product_id)
    if not product:
        return types.InlineKeyboardMarkup()
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📝 Изменить название", callback_data=f"admin_change_name_{product_id}"),
        types.InlineKeyboardButton("💰 Изменить цену", callback_data=f"admin_change_price_{product_id}")
    )
    markup.add(
        types.InlineKeyboardButton("📏 Изменить размеры", callback_data=f"admin_change_sizes_{product_id}"),
        types.InlineKeyboardButton("🖼 Изменить фото", callback_data=f"admin_change_photo_{product_id}")
    )
    markup.add(
        types.InlineKeyboardButton("📦 Наличие размеров", callback_data=f"admin_stock_{product_id}")
    )
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin_edit_select"))
    return markup

def get_admin_delete_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    # Только категории с товарами
    for category in ["shoes", "clothes"]:
        for product in PRODUCTS.get(category, []):
            if isinstance(product, dict) and 'id' in product:
                markup.add(types.InlineKeyboardButton(
                    f"❌ {product['name']} - {product['price']} ₽",
                    callback_data=f"admin_del_prod_{product['id']}"
                ))
    
    if len(markup.keyboard) == 0:
        markup.add(types.InlineKeyboardButton("Нет товаров для удаления", callback_data="admin_panel"))
    else:
        markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data="admin_panel"))
    return markup

def get_cart_menu():
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(types.InlineKeyboardButton("📦 Оформить заказ", callback_data="cart_checkout"))
    mk.add(types.InlineKeyboardButton("🧹 Очистить корзину", callback_data="cart_clear"))
    mk.add(types.InlineKeyboardButton("◀️ Главное меню", callback_data="back_main"))
    return mk

def get_faq_menu():
    mk = types.InlineKeyboardMarkup(row_width=1)
    mk.add(types.InlineKeyboardButton("1. Как сделать заказ?", callback_data="faq_0"))
    mk.add(types.InlineKeyboardButton("2. Способы оплаты?", callback_data="faq_1"))
    mk.add(types.InlineKeyboardButton("3. Самовывоз?", callback_data="faq_2"))
    mk.add(types.InlineKeyboardButton("4. Возврат?", callback_data="faq_3"))
    mk.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_main"))
    return mk

def get_support_admin_menu(ticket_id, status):
    mk = types.InlineKeyboardMarkup(row_width=2)
    
    if status == "new":
        mk.add(types.InlineKeyboardButton("🛠 Взять в работу", callback_data=f"support_take::{ticket_id}"))
    elif status == "in_work":
        mk.add(types.InlineKeyboardButton("✉️ Ответить", callback_data=f"support_reply::{ticket_id}"))
        mk.add(types.InlineKeyboardButton("✅ Закрыть тикет", callback_data=f"support_close::{ticket_id}"))
    
    return mk

# --- ФУНКЦИИ КАТАЛОГА ---

def size_menu(category: str) -> types.InlineKeyboardMarkup:
    available_sizes = set()
    for p in PRODUCTS.get(category, []):
        stock = p.get("stock", {})
        for size in stock.keys():
            available_sizes.add(size)
    
    if not available_sizes:
        return None

    def sort_key(s):
        s = s.strip()
        if s.isdigit():
            return (0, int(s))
        order = {"S": 1, "M": 2, "L": 3, "XL": 4, "XXL": 5}
        return (1, order.get(s.upper(), 999))

    sizes = sorted(available_sizes, key=sort_key)
    mk = types.InlineKeyboardMarkup(row_width=4)
    row = []
    for s in sizes:
        row.append(types.InlineKeyboardButton(s, callback_data=f"select_size_{category}_{s}"))
        if len(row) == 4:
            mk.add(*row)
            row = []
    if row:
        mk.add(*row)
    mk.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_main"))
    return mk

def show_browse(call, category: str, size: str, idx: int):
    """Показывает конкретный товар с кнопками навигации и заказа."""
    filtered = [p for p in PRODUCTS.get(category, []) if size in p.get("sizes", [])]
    
    if not filtered or idx < 0 or idx >= len(filtered):
        bot.answer_callback_query(call.id, "Товар не найден.")
        return
        
    product = filtered[idx]

    in_stock = product.get('stock', {}).get(size, True)
    stock_text = '✅ В наличии' if in_stock else '❌ Нет в наличии'

    caption = (f"{'👟 Обувь' if category == 'shoes' else '👕 Одежда'} | Размер: {size}\n\n"
           f"*{product['name']}*\n"
           f"💰 {product['price']} ₽\n"
           f"{stock_text}")

    mk = types.InlineKeyboardMarkup(row_width=3)
    # ✅ Если товара нет в наличии — отключаем кнопки заказа
    if not in_stock:
    # Убираем кнопки заказа
      mk.keyboard = [
        row for row in mk.keyboard if not any(
            btn.callback_data and ('order_' in btn.callback_data or 'cart_add::' in btn.callback_data)
            for btn in row
        )
    ]

    # Кнопки навигации
    nav_row = []
    if idx > 0:
        nav_row.append(types.InlineKeyboardButton("◀️ Назад", callback_data=f"browse_{category}_{size}_{idx - 1}"))
    
    nav_row.append(types.InlineKeyboardButton(f"{idx + 1}/{len(filtered)}", callback_data="noop"))
    
    if idx < len(filtered) - 1:
        nav_row.append(types.InlineKeyboardButton("Далее ▶️", callback_data=f"browse_{category}_{size}_{idx + 1}"))
    
    mk.add(*nav_row)
    
    # Кнопки действий
    mk.add(
        types.InlineKeyboardButton("🛒 Заказать", callback_data=f"order_{product['id']}_{size}"),
        types.InlineKeyboardButton("➕ В корзину", callback_data=f"cart_add::{product['id']}::{size}")
    )
    
    # Кнопки возврата
    # ИСПРАВЛЕНО: Используем cat_{category} для возврата к меню размеров
    mk.add(types.InlineKeyboardButton("↩️ К размерам", callback_data=f"cat_{category}"))
    mk.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="back_main"))

    # Отправляем фото
    send_one_photo(
        call.message.chat.id,
        product["image"],
        caption=caption,
        reply_markup=mk,
        user_id=call.from_user.id
    )

# --- СООБЩЕНИЯ ---

def send_one_msg(chat_id, text, reply_markup=None, parse_mode="Markdown", user_id=None):
    """Удаляет предыдущее сообщение бота и шлёт новое."""
    if user_id and last_bot_msg.get(user_id):
        try:
            bot.delete_message(chat_id, last_bot_msg[user_id])
        except:
            pass
    mid = bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup).message_id
    if user_id:
        last_bot_msg[user_id] = mid
    return mid


def send_one_photo(chat_id, photo, caption, reply_markup=None, parse_mode="Markdown", user_id=None):
    """Удаляет предыдущее сообщение бота и шлёт новое фото."""
    if user_id and last_bot_msg.get(user_id):
        try:
            bot.delete_message(chat_id, last_bot_msg[user_id])
        except:
            pass
    mid = bot.send_photo(chat_id, photo, caption=caption, parse_mode=parse_mode,
                         reply_markup=reply_markup).message_id
    if user_id:
        last_bot_msg[user_id] = mid
    return mid


def send_welcome(chat_id, user_id):
    """Главное меню: медиа-файл или текст, но всегда 1 сообщение."""
    welcome = PRODUCTS.get("welcome")
    if welcome and welcome.get("file_id"):
        kwargs = dict(chat_id=chat_id, caption=welcome["caption"],
                      parse_mode="Markdown", reply_markup=get_reply_main_menu())
        if user_id and last_bot_msg.get(user_id):
            try:
                bot.delete_message(chat_id, last_bot_msg[user_id])
            except:
                pass
        if welcome["type"] == "photo":
            mid = bot.send_photo(photo=welcome["file_id"], **kwargs).message_id
        elif welcome["type"] == "video":
            mid = bot.send_video(video=welcome["file_id"], **kwargs).message_id
        elif welcome["type"] == "animation":
            mid = bot.send_animation(animation=welcome["file_id"], **kwargs).message_id
        else:
            mid = send_one_msg(
                chat_id,
                "🏪 *Добро пожаловать в Orphelins Dorés!*\n\nВыберите категорию:",
                parse_mode="Markdown",
                reply_markup=get_reply_main_menu(),
                user_id=user_id
            )
    else:
        mid = send_one_msg(
    chat_id,
    "🏪 *Добро пожаловать в Orphelins Dorés!*\n\nВыберите категорию:",
    parse_mode="Markdown",
    reply_markup=get_reply_main_menu(),
    user_id=user_id
)
    if user_id:
        last_bot_msg[user_id] = mid
    return mid

# --- ОБРАБОТЧИКИ REPLY-КНОПОК ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_stock_"))
def admin_edit_stock(call):
    if not is_admin(call.from_user.id):
        return
    product_id = int(call.data.split("_")[2])
    product = find_product_by_id(product_id)
    if not product or 'stock' not in product:
        bot.answer_callback_query(call.id, "Нет размеров")
        return

    markup = types.InlineKeyboardMarkup(row_width=3)
    for size, available in product['stock'].items():
        status = "✅" if available else "❌"
        new_val = 0 if available else 1
        markup.add(types.InlineKeyboardButton(
            f"{status} {size}",
            callback_data=f"toggle_stock_{product_id}_{size}_{new_val}"
        ))
    markup.add(types.InlineKeyboardButton("◀️ Назад", callback_data=f"admin_edit_prod_{product_id}"))

    bot.edit_message_text(
        f"📦 *Наличие: {product['name']}*\n\nНажмите на размер для переключения:",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("toggle_stock_"))
def toggle_stock_handler(call):
    if not is_admin(call.from_user.id):
        return
    try:
        parts = call.data.split("_")
        product_id = int(parts[2])
        size = parts[3]
        new_status = bool(int(parts[4]))

        for category in ["shoes", "clothes"]:
            for product in PRODUCTS.get(category, []):
                if product.get('id') == product_id:
                    if 'stock' in product and size in product['stock']:
                        product['stock'][size] = new_status
                        save_products()
                        bot.answer_callback_query(call.id, f"{size}: {'в наличии' if new_status else 'нет в наличии'}")
                        # Обновляем меню
                        admin_edit_stock(call)
                        return
        bot.answer_callback_query(call.id, "Ошибка")
    except Exception as e:
        logger.error(f"Ошибка переключения наличия: {e}")
        bot.answer_callback_query(call.id, "Ошибка")

@bot.message_handler(commands=['start'])
def send_welcome_command(message):
    send_welcome(message.chat.id, message.from_user.id)

@bot.message_handler(func=lambda message: message.text == "👟 Смотреть обувь")
def show_shoes_reply(message):
    logger.info("[DEBUG] 👟 Смотреть обувь")
    mk = size_menu("shoes")
    if mk:
        send_one_msg(message.chat.id, "👟 Выберите размер обуви:",
             reply_markup=mk, user_id=message.from_user.id)
    else:
        send_one_msg(message.chat.id, "Товары скоро появятся!",
                     reply_markup=get_reply_main_menu(), user_id=message.from_user.id)
        
@bot.message_handler(func=lambda message: message.text == "👕 Смотреть одежду")
def show_clothes_reply(message):
    logger.info("[DEBUG] 👕 Смотреть одежду")
    mk = size_menu("clothes")
    if mk:
        send_one_msg(message.chat.id, "👕 Выберите размер одежды:",
             reply_markup=mk, user_id=message.from_user.id)
    else:
        send_one_msg(message.chat.id, "Товары скоро появятся!",
                     reply_markup=get_reply_main_menu(), user_id=message.from_user.id)

@bot.message_handler(func=lambda message: message.text == "🛒 Корзина")
def show_cart(message):
    user_id = message.from_user.id
    cart = CARTS.get(user_id, [])
    if not cart:
        send_one_msg(
            message.chat.id,
            "🛒 Ваша корзина пуста",
            reply_markup=get_reply_main_menu(),
            user_id=message.from_user.id
        )
        return

    total = 0
    text = "🛒 *Ваша корзина:*\n\n"
    for item in cart:
        price = item["price"]
        total += price
        text += f"• {item['name']} ({item['size']}) — {price} ₽\n"

    text += f"\n*Итого: {total} ₽*"
    mk = get_cart_menu()
    send_one_msg(
        message.chat.id,
        text,
        parse_mode="Markdown",
        reply_markup=mk,
        user_id=message.from_user.id
    )

@bot.message_handler(func=lambda message: message.text == "❓ FAQ")
def faq_reply(message):
    logger.info("[DEBUG] ❓ FAQ")
    send_one_msg(
        message.chat.id,
        "❓ *Часто задаваемые вопросы*",
        parse_mode="Markdown",
        reply_markup=get_faq_menu(),
        user_id=message.from_user.id
    )

@bot.message_handler(func=lambda message: message.text == "🔥 Sale до -50%")
def sale_reply(message):
    logger.info("[DEBUG] 🔥 Sale до -50%")
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_main"))
    send_one_msg(
        message.chat.id,
        "🔥 Раздел *Sale до -50%* скоро появится!",
        parse_mode="Markdown",
        reply_markup=mk,
        user_id=message.from_user.id
    )

@bot.message_handler(func=lambda message: message.text == "🆘 Поддержка")
def support_reply(message):
    user_id = message.from_user.id
    
    # 1. Проверка кулдауна
    if user_id in user_data and 'support_cooldown_until' in user_data[user_id]:
        cooldown_until = user_data[user_id]['support_cooldown_until']
        if datetime.now() < cooldown_until:
            remaining = cooldown_until - datetime.now()
            minutes = int(remaining.total_seconds() // 60)
            seconds = int(remaining.total_seconds() % 60)
            text = f"⏳ *Подождите!* Вы сможете создать новый тикет через {minutes} мин. {seconds} сек."
            send_one_msg(message.chat.id, text, parse_mode="Markdown", user_id=user_id)
            return

    # 2. Проверка активного тикета
    active_ticket = next((t for t in SUPPORT_TICKETS.values() if t['user_id'] == user_id and t['status'] != 'closed'), None)
    if active_ticket:
        text = f"⚠️ У вас уже есть активный тикет *#{active_ticket['id']}* со статусом: *{active_ticket['status']}*.\nДождитесь ответа или закрытия тикета."
        send_one_msg(message.chat.id, text, parse_mode="Markdown", user_id=user_id)
        return

    # 3. Начало создания тикета
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]['waiting_for'] = 'support_message'
    
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel"))
    
    send_one_msg(
        message.chat.id,
        "✍️ *Опишите вашу проблему или вопрос.*",
        parse_mode="Markdown",
        reply_markup=mk,
        user_id=user_id
    )


@bot.callback_query_handler(func=lambda c: c.data == "cart_checkout")
def cart_checkout_handler(call):
    user_id = call.from_user.id
    cart = CARTS.get(user_id, [])
   
    if not cart:
        bot.answer_callback_query(call.id, "Корзина пуста!")
        return
   
    # Проверка имени/username
    user = call.from_user
    if not user.username and not (user.first_name or user.last_name):
        bot.answer_callback_query(call.id, "❌ Установите имя или username в настройках Telegram")
        return
   
    # Защита от спама
    current_time = int(time.time())
    if user_id not in user_data:
        user_data[user_id] = {}
    last_order_time = user_data[user_id].get('last_order_time', 0)
    if current_time - last_order_time < 30:
        bot.answer_callback_query(call.id, "⏳ Подождите 30 секунд перед следующим заказом")
        return
    user_data[user_id]['last_order_time'] = current_time
   
    global NEXT_ORDER_ID
    order_id = NEXT_ORDER_ID
    NEXT_ORDER_ID += 1
   
    username, full_name = get_user_info(user)
    total_price = sum(item['price'] for item in cart)
   
    ORDERS[order_id] = {
        "user_id": user_id,
        "username": username,
        "full_name": full_name,
        "items": cart.copy(),
        "status": "new",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_orders()
   
    # Очищаем корзину
    if user_id in CARTS:
        del CARTS[user_id]
   
    # === УВЕДОМЛЕНИЕ В АДМИН-ГРУППУ ===
    items_text = "\n".join(
        f"• {item['name']} ({item['size']}) — {item['price']} ₽" for item in cart
    )
   
    admin_text = (
        f"🔔 *Новый заказ #{order_id} (из корзины)*\n\n"
        f"👤 Клиент: {full_name}\n"
        f"📱 Username: {username}\n"
        f"🆔 User ID: `{user_id}`\n\n"
        f"🛍 *Товары ({len(cart)} шт.):*\n{items_text}\n"
        f"*Итого: {total_price} ₽*"
    )
   
    if user_id in ADMIN_IDS:
        admin_text += "\n⚠️ *Внимание: тестовый заказ от админа!*"
   
    # Создаём разметку и безопасно пытаемся добавить кнопку "Написать клиенту"
    mk_admin = types.InlineKeyboardMarkup()
    button_added = False
    try:
        # Тестовая кнопка для проверки ошибки приватности
        types.InlineKeyboardButton("test", url=f"tg://user?id={user_id}")
        mk_admin.add(
            types.InlineKeyboardButton("✉️ Написать клиенту", url=f"tg://user?id={user_id}")
        )
        button_added = True
    except Exception as e:
        if "BUTTON_USER_PRIVACY_RESTRICTED" in str(e):
            pass  # Клиент запретил — отправляем без кнопки
        else:
            logger.error(f"Неожиданная ошибка при создании кнопки ЛС: {e}")
            # Не прерываем выполнение из-за редкой ошибки
   
    # Отправляем уведомление в группу
    notification_success = False
    try:
        # Пробуем отправить с фото первого товара (если есть)
        first_item = cart[0]
        product = find_product_by_id(first_item.get('product_id'))
        if product and product.get("image"):
            bot.send_photo(
                ADMIN_GROUP_ID,
                product["image"],
                caption=admin_text,
                parse_mode="Markdown",
                reply_markup=mk_admin if button_added else None
            )
        else:
            bot.send_message(
                ADMIN_GROUP_ID,
                admin_text,
                parse_mode="Markdown",
                reply_markup=mk_admin if button_added else None
            )
        notification_success = True
    except Exception as e:
        logger.error(f"Ошибка отправки заказа в группу: {e}")
   
    # Ответ клиенту в popup
    if notification_success:
        bot.answer_callback_query(call.id, "✅ Заказ оформлен и отправлен менеджеру!")
    else:
        bot.answer_callback_query(call.id, "✅ Заказ оформлен, но не удалось уведомить админов")
   
    # Подтверждение клиенту в чате
    client_text = (
        f"✅ *Заказ #{order_id} оформлен!*\n\n"
        f"Товаров: {len(cart)}\n"
        f"Сумма: {total_price} ₽\n\n"
        f"Менеджер свяжется с вами в ближайшее время.\n"
        f"Спасибо за покупку! ❤️"
    )
   
    mk_client = types.InlineKeyboardMarkup()
    mk_client.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="back_main"))
   
    safe_edit_message(call, client_text, reply_markup=mk_client)


@bot.callback_query_handler(func=lambda c: c.data == "cart_clear")
def cart_clear_handler(call):
    user_id = call.from_user.id
    
    if user_id in CARTS:
        del CARTS[user_id]
        bot.answer_callback_query(call.id, "🧹 Корзина очищена!")
    else:
        bot.answer_callback_query(call.id, "Корзина и так пуста")
   
    # Показываем пустую корзину
    send_one_msg(
        call.message.chat.id,
        "🛒 Ваша корзина пуста",
        reply_markup=get_reply_main_menu(),
        user_id=user_id
    )

# --- ОБРАБОТЧИКИ CALLBACK-КНОПОК ---

@bot.callback_query_handler(func=lambda c: c.data == "admin_cancel")
def admin_cancel(call):
    user_id = call.from_user.id
    if user_id in user_data:
        prev_state = user_data.pop(user_id, None)
        logger.info(f"Админ {user_id} отменил операцию: {prev_state}")
        bot.answer_callback_query(call.id, "⏹️ Операция отменена", show_alert=False)
        # Возвращаем в главное меню, если отмена была из главного меню
        if prev_state and prev_state.get('waiting_for') in ['support_message', 'name_new_shoes', 'name_new_clothes']:
            send_welcome(call.message.chat.id, user_id)
    else:
        bot.answer_callback_query(call.id, "⏹️ Нет активных операций", show_alert=False)

@bot.callback_query_handler(func=lambda c: c.data.startswith("faq_"))
def faq_handler(call):
    try:
        index = int(call.data.split("_")[1])
        text = FAQ_ANSWERS[index]
        
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(types.InlineKeyboardButton("◀️ Назад к FAQ", callback_data="back_faq"))
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=mk
        )
    except Exception as e:
        logger.error(f"Ошибка в faq_handler: {e}")
        bot.answer_callback_query(call.id, "⚠️ Произошла ошибка.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("back_"))
def back_handler(call):
    data = call.data.split("_")[1]
    
    if data == "main":
        send_welcome(call.message.chat.id, call.from_user.id)
        return
    
    if data == "faq":
        faq_reply(call.message)
        return
    
    # Логика возврата к выбору размеров (cat_shoes, cat_clothes)
    if data in ["shoes", "clothes"]:
        category = data
        mk = size_menu(category)
        if mk:
            bot.edit_message_text(
                f"{'👟 Выберите размер обуви:' if category == 'shoes' else '👕 Выберите размер одежды:'}",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=mk
            )
        else:
            bot.answer_callback_query(call.id, "Товары скоро появятся!")
        return

# --- КАТАЛОГ И НАВИГАЦИЯ (ИСПРАВЛЕНО) ---

@bot.callback_query_handler(func=lambda c: c.data.startswith("cat_"))
def cat_handler(call):
    """Обработчик для кнопки 'К размерам' из show_browse."""
    try:
        category = call.data.split("_")[1]
        mk = size_menu(category)
        if mk:
            # Используем edit_message_caption, так как show_browse отправляет фото
            bot.edit_message_caption(
                f"{'👟 Выберите размер обуви:' if category == 'shoes' else '👕 Выберите размер одежды:'}",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=mk
            )
        else:
            bot.answer_callback_query(call.id, "Товары скоро появятся!")
    except Exception as e:
        logger.error(f"Ошибка в cat_handler: {e}")
        bot.answer_callback_query(call.id, "⚠️ Произошла ошибка.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("select_size_"))
def select_size_handler(call):
    try:
        # select_size_{category}_{size}
        parts = call.data.split("_")
        category = parts[2]
        size = "_".join(parts[3:])
        
        # Удаляем старое сообщение и показываем первый товар
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_browse(call, category, size, 0)
        
    except Exception as e:
        logger.error(f"Ошибка в select_size_handler: {e}")
        bot.answer_callback_query(call.id, "⚠️ Произошла ошибка при выборе размера.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("browse_"))
def browse_handler(call):
    try:
        # browse_{category}_{size}_{idx}
        parts = call.data.split("_")
        category = parts[1]
        size = parts[2]
        idx = int(parts[3])
        
        # Удаляем старое сообщение и показываем следующий/предыдущий товар
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_browse(call, category, size, idx)
        
    except Exception as e:
        logger.error(f"Ошибка в browse_handler: {e}")
        bot.answer_callback_query(call.id, "⚠️ Произошла ошибка при навигации.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("order_") and not c.data.startswith(("order_view_", "order_status_", "order_message_")))
def order_from_product_handler(call):
    """Оформление быстрого заказа из каталога: order_{product_id}_{size}"""
    try:
        parts = call.data.split("_")
        if len(parts) != 3:
            return  # Игнорируем, если не 3 части
        
        _, product_id_str, size = parts
        product_id = int(product_id_str)
        product = find_product_by_id(product_id)
        
        if not product:
            bot.answer_callback_query(call.id, "Товар не найден")
            return

        # === ВСЁ ОСТАЛЬНОЕ БЕЗ ИЗМЕНЕНИЙ ===
        user = call.from_user
        if not user.username and not (user.first_name or user.last_name):
            bot.answer_callback_query(call.id, "❌ Установите имя или username в настройках Telegram")
            return

        user_id = call.from_user.id
        if user_id not in user_data:
            user_data[user_id] = {}
        
        last_order_time = user_data[user_id].get("last_order_time", 0)
        current_time = int(call.message.date)
        if current_time - last_order_time < 30:
            bot.answer_callback_query(call.id, "⏳ Подождите 30 секунд перед следующим заказом")
            return
        user_data[user_id]["last_order_time"] = current_time

        global NEXT_ORDER_ID
        order_id = NEXT_ORDER_ID
        NEXT_ORDER_ID += 1
        
        username, full_name = get_user_info(call.from_user)

        ORDERS[order_id] = {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "items": [{
                "product_id": product_id,
                "size": size,
                "name": product["name"],
                "price": product["price"]
            }],
            "status": "new",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_orders()

        admin_text = (
            f"🔔 *Новый заказ #{order_id} (Быстрый заказ)*\n"
            f"👤 Клиент: {full_name}\n"
            f"📱 Username: {username}\n"
            f"🆔 User ID: `{user_id}`\n"
            f"🛍 Товар: {product['name']}\n"
            f"📏 Размер: {size}\n"
            f"💰 Цена: {product['price']} ₽"
        )

        mk_admin = types.InlineKeyboardMarkup()
        try:
            mk_admin.add(types.InlineKeyboardButton("✉️ Написать в ЛС", callback_data=f"order_message_{order_id}"))
        except Exception as e:
            if "BUTTON_USER_PRIVACY_RESTRICTED" in str(e):
                pass  # Просто без кнопки
            else:
                raise  # Если другая ошибка — поднимаем её

        if user_id in ADMIN_IDS:
            admin_text += "\n⚠️ *Внимание: это тестовый заказ от админа!*"

        try:
            if product.get("image"):
                bot.send_photo(ADMIN_GROUP_ID, product["image"], caption=admin_text, parse_mode="Markdown", reply_markup=mk_admin)
            else:
                bot.send_message(ADMIN_GROUP_ID, admin_text, parse_mode="Markdown", reply_markup=mk_admin)
            bot.answer_callback_query(call.id, "✅ Заказ отправлен!")
        except Exception as e:
            logger.error(f"Ошибка отправки заказа в группу: {e}")
            bot.answer_callback_query(call.id, "Не удалось отправить заказ")

        client_text = (
            f"✅ *Заказ #{order_id} оформлен!*\n"
            f"Товар: {product['name']}\n"
            f"Размер: {size}\n"
            f"Цена: {product['price']} ₽\n"
            f"Наш менеджер свяжется с вами в ближайшее время.\n"
            f"Контакты:\n📱 Telegram: @sonhayy"
        )
        mk_client = types.InlineKeyboardMarkup()
        mk_client.add(types.InlineKeyboardButton("◀️ Главное меню", callback_data="back_main"))

        try:
            bot.edit_message_caption(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                caption=client_text,
                parse_mode="Markdown",
                reply_markup=mk_client
            )
        except:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=client_text,
                parse_mode="Markdown",
                reply_markup=mk_client
            )

    except Exception as e:
        logger.error(f"Ошибка в order_from_product_handler: {e}")
        bot.answer_callback_query(call.id, "⚠️ Произошла ошибка при оформлении заказа.")

# --- АДМИН-ПАНЕЛЬ (ИСПРАВЛЕНО) ---

@bot.message_handler(commands=['admin'])
def admin_panel_command(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        bot.send_message(message.chat.id, "❌ У вас нет доступа к админ-панели")
        return
    bot.send_message(
        message.chat.id,
        "🔧 Админ-панель",
        reply_markup=get_admin_reply_menu()
    )


@bot.callback_query_handler(func=lambda c: c.data == "admin_stats")
def admin_stats(call):
    if not is_admin(call.from_user.id):
        return
    
    # Собираем данные
    total_orders = len(ORDERS)
    status_count = {
        'new': 0,
        'in_processing': 0,
        'sent': 0,
        'completed': 0
    }
    total_revenue = 0
    unique_clients = set()
    product_sales = {}  # product_id → (кол-во, сумма)
    
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    week_start = today_start - timedelta(days=7)
    
    today_orders = 0
    today_revenue = 0
    week_orders = 0
    week_revenue = 0
    
    for order in ORDERS.values():
        order_date = datetime.strptime(order['created_at'], "%Y-%m-%d %H:%M:%S")
        status = order['status']
        # нормализуем статус, если вдруг в БД лежит что-то другое
        status = {
            'in': 'in_processing',      # старый вариант
            'new': 'new',
            'in_processing': 'in_processing',
            'sent': 'sent',
            'completed': 'completed'
        }.get(status, 'new')
        status_count[status] += 1
        
        items_total = sum(item['price'] for item in order['items'])
        total_revenue += items_total
        
        unique_clients.add(order['user_id'])
        
        # За сегодня и неделю
        if order_date >= today_start:
            today_orders += 1
            today_revenue += items_total
        if order_date >= week_start:
            week_orders += 1
            week_revenue += items_total
        
        # Топ товаров
        for item in order['items']:
            pid = item['product_id']
            name = item['name']
            price = item['price']
            key = (pid, name)
            if key not in product_sales:
                product_sales[key] = {'count': 0, 'revenue': 0}
            product_sales[key]['count'] += 1
            product_sales[key]['revenue'] += price
    
    # Средний чек
    avg_check = round(total_revenue / total_orders, 2) if total_orders > 0 else 0
    
    # Топ-5 товаров
    top_products = sorted(product_sales.items(), key=lambda x: x[1]['count'], reverse=True)[:5]
    top_text = ""
    for i, ((pid, name), stats) in enumerate(top_products, 1):
        top_text += f"{i}. {name} — {stats['count']} шт. ({stats['revenue']} ₽)\n"
    if not top_text:
        top_text = "Пока нет продаж"
    
    # Основной текст
    text = (
        f"📊 *Статистика магазина*\n\n"
        f"👥 Уникальных клиентов: *{len(unique_clients)}*\n"
        f"🛍 Всего заказов: *{total_orders}*\n"
        f"   • Новые: {status_count['new']}\n"
        f"   • В обработке: {status_count['in_processing']}\n"
        f"   • Отправленные: {status_count['sent']}\n"
        f"   • Завершённые: {status_count['completed']}\n\n"
        f"💰 Выручка всего: *{total_revenue} ₽*\n"
        f"📈 Средний чек: *{avg_check} ₽*\n\n"
        f"🔥 *Топ-5 товаров:*\n{top_text}\n\n"
        f"📅 За сегодня: {today_orders} заказов / {today_revenue} ₽\n"
        f"🗓 За неделю: {week_orders} заказов / {week_revenue} ₽"
    )
    
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="admin_stats"))
    mk.add(types.InlineKeyboardButton("◀️ Назад в админ-панель", callback_data="admin_panel"))
    
    safe_edit_message(call, text, reply_markup=mk)

def safe_edit_message(call, text, reply_markup=None):
    try:
        bot.edit_message_text(
            text=text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        return
    except Exception as e1:
        # Если было фото — попробуем edit caption
        try:
            bot.edit_message_caption(
                caption=text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            return
        except Exception as e2:
            pass

    # Если ничего не вышло — удаляем старое и шлём новое
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=reply_markup)

@bot.callback_query_handler(func=lambda c: c.data == "admin_panel")
def admin_panel_callback(call):
    if not is_admin(call.from_user.id):
        return
    safe_edit_message(
        call,
        "🔧 *Админ-панель*",
        reply_markup=get_admin_reply_menu
    )

@bot.callback_query_handler(func=lambda c: c.data == "admin_orders")
def admin_orders_list(call):
    if not is_admin(call.from_user.id):
        return
    
    total_orders = len(ORDERS)
    new_orders = sum(1 for o in ORDERS.values() if o.get('status') == 'new')
    
    text = (
        f"🚚 *Управление заказами*\n\n"
        f"Всего заказов: *{total_orders}*\n"
        f"Новых: *{new_orders}*\n\n"
    )
    
    if total_orders == 0:
        text += "Нет заказов на данный момент."
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("◀️ Назад в админ-панель", callback_data="admin_panel"))
    else:
        text += "Выберите заказ для просмотра:"
        mk = get_orders_list_menu()
    
    # Универсальная функция — пробует все варианты редактирования
    safe_edit_message(call, text, reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("order_view_"))
def admin_order_view(call):
    if not is_admin(call.from_user.id):
        return
    try:
        order_id = int(call.data.split("_")[2])
        order = ORDERS.get(order_id)
        if not order:
            bot.answer_callback_query(call.id, "Заказ не найден")
            return

        # Формируем текст заказа
        status_text = {
            'new': '🆕 Новый',
            'in_processing': '🔄 В обработке',
            'sent': '🚚 Отправлен',
            'completed': '✅ Завершён'
        }.get(order['status'], '❓ Неизвестно')

        items_text = ""
        total_price = 0
        for item in order['items']:
            items_text += f"• {item['name']} ({item['size']}) — {item['price']} ₽\n"
            total_price += item['price']

        user_id = order['user_id']

        # Формируем текст
        text = (
            f"📦 *Заказ #{order_id}*\n\n"
            f"Статус: *{status_text}*\n"
            f"Дата: {order['created_at']}\n\n"
            f"👤 Клиент: {order['full_name']}\n"
            f"📱 Username: {order['username']}\n"
            f"🆔 ID: `{user_id}`\n\n"
            f"🛍 *Товары:*\n{items_text}\n"
            f"*Итого: {total_price} ₽*"
        )

        # Создаём разметку БЕЗ кнопки "Написать клиенту"
        markup = types.InlineKeyboardMarkup(row_width=1)

        # Кнопки статуса
        if order['status'] == 'new':
            markup.add(types.InlineKeyboardButton("🔄 В обработке", callback_data=f"order_status_{order_id}_in_processing"))
        elif order['status'] == 'in_processing':
            markup.row(
                types.InlineKeyboardButton("🚚 Отправлен", callback_data=f"order_status_{order_id}_sent"),
                types.InlineKeyboardButton("⬅️ В новые", callback_data=f"order_status_{order_id}_new")
            )
        elif order['status'] == 'sent':
            markup.row(
                types.InlineKeyboardButton("✅ Завершён", callback_data=f"order_status_{order_id}_completed"),
                types.InlineKeyboardButton("⬅️ В обработке", callback_data=f"order_status_{order_id}_in_processing")
            )
        elif order['status'] == 'completed':
            markup.add(types.InlineKeyboardButton("🔄 Вернуть в обработку", callback_data=f"order_status_{order_id}_in_processing"))

        markup.add(types.InlineKeyboardButton("🗑 Удалить заказ", callback_data=f"order_delete_{order_id}"))
        markup.add(types.InlineKeyboardButton("◀️ Назад к списку", callback_data="admin_orders"))

        # Безопасно редактируем/отправляем
        safe_edit_message(call, text, reply_markup=markup)

        bot.answer_callback_query(call.id, "Заказ открыт")

    except Exception as e:
        logger.error(f"Критическая ошибка в admin_order_view: {e}")
        bot.answer_callback_query(call.id, "Ошибка открытия заказа")



@bot.callback_query_handler(func=lambda c: c.data.startswith("cart_add::"))
def cart_add_handler(call):
    """Добавление товара в корзину: cart_add::{product_id}::{size}"""
    try:
        parts = call.data.split("::")
        if len(parts) != 3:
            bot.answer_callback_query(call.id, "Ошибка добавления")
            return
       
        _, product_id_str, size = parts
        product_id = int(product_id_str)
        product = find_product_by_id(product_id)
       
        if not product:
            bot.answer_callback_query(call.id, "Товар не найден")
            return
       
        user_id = call.from_user.id
        if user_id not in CARTS:
            CARTS[user_id] = []
       
        # Проверяем, есть ли уже такой товар с таким размером
        for item in CARTS[user_id]:
            if item['product_id'] == product_id and item['size'] == size:
                bot.answer_callback_query(call.id, "Этот товар с таким размером уже в корзине!")
                return
       
        # Добавляем
        CARTS[user_id].append({
            "product_id": product_id,
            "name": product["name"],
            "price": product["price"],
            "size": size
        })
       
        bot.answer_callback_query(call.id, f"✅ {product['name']} ({size}) добавлен в корзину!")
       
        # Можно обновить сообщение с товаром, чтобы показать актуальные кнопки
        # Или просто оставить как есть — пользователь увидит при переходе в корзину
       
    except Exception as e:
        logger.error(f"Ошибка при добавлении в корзину: {e}")
        bot.answer_callback_query(call.id, "Ошибка при добавлении")


@bot.callback_query_handler(func=lambda c: c.data.startswith("order_status_"))
def admin_order_change_status(call):
    if not is_admin(call.from_user.id):
        return
    
    try:
        parts = call.data.split("_")
        order_id = int(parts[2])
        new_status = parts[3]
        
        order = ORDERS.get(order_id)
        if not order:
            bot.answer_callback_query(call.id, "Заказ не найден")
            return
        
        old_status = order['status']
        order['status'] = new_status
        save_orders()
        
        status_names = {
            'new': '🆕 Новый',
            'in_processing': '🔄 В обработке',
            'sent': '🚚 Отправлен',
            'completed': '✅ Завершён'
        }
        
        bot.answer_callback_query(call.id, f"Статус изменён: {status_names.get(new_status)}")
        
        # Уведомляем клиента о смене статуса (опционально, но круто!)
        status_client_text = {
            'in_processing': '🔄 Ваш заказ взят в обработку!',
            'sent': '🚚 Ваш заказ отправлен!',
            'completed': '✅ Ваш заказ завершён! Спасибо за покупку ❤️'
        }
        if new_status in status_client_text:
            try:
                bot.send_message(order['user_id'], status_client_text[new_status])
            except:
                pass  # Если пользователь заблокировал бота — игнорируем
        
        # Обновляем детали заказа (кнопки и статус)
        fake_call = call
        fake_call.data = f"order_view_{order_id}"
        admin_order_view(fake_call)
        
    except Exception as e:
        logger.error(f"Ошибка при смене статуса заказа: {e}")
        bot.answer_callback_query(call.id, "Ошибка при изменении статуса")






@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_edit_category_"))
def admin_edit_category(call):
    if not is_admin(call.from_user.id): return
    category = call.data.split("_")[3]  # shoes или clothes
    bot.edit_message_text(
        f"✏️ Редактирование {'обуви' if category == 'shoes' else 'одежды'}\n\nВыберите товар:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=get_admin_edit_menu(category)
    )


@bot.message_handler(func=lambda message: message.text == "➕ Добавить товар")
def admin_add_select_reply(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(
        message.chat.id,
        "Выберите категорию:",
        reply_markup=get_admin_category_menu()
    )

@bot.message_handler(func=lambda message: message.text in ["👟 Обувь", "👕 Одежда"] and user_data.get(message.from_user.id, {}).get('waiting_for') is None)
def admin_add_category_reply(message):
    if not is_admin(message.from_user.id):
        return
    category = "shoes" if message.text == "👟 Обувь" else "clothes"
    user_data[message.from_user.id] = {'waiting_for': f'name_new_{category}'}
    bot.send_message(message.chat.id, f"➕ Добавление {category}\n\nВведите название товара:")

@bot.message_handler(func=lambda message: message.text == "🗑 Удалить товар")
def admin_delete_select_reply(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(
        message.chat.id,
        "Выберите товар для удаления:",
        reply_markup=get_admin_delete_products_reply_menu()
    )


def get_admin_delete_products_reply_menu():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    for category in ["shoes", "clothes"]:
        for product in PRODUCTS.get(category, []):
            if isinstance(product, dict) and 'name' in product:
                mk.add(f"❌ {product['name']} - {product['price']} ₽")
    mk.add("◀️ Назад")
    return mk


@bot.message_handler(func=lambda message: message.text.startswith("❌") and " - " in message.text and "₽" in message.text)
def admin_delete_product_by_name(message):
    if not is_admin(message.from_user.id):
        return

    name = message.text.split(" - ")[0].replace("❌", "").strip()
    deleted = False
    for category in ["shoes", "clothes"]:
        for product in PRODUCTS.get(category, []):
            if isinstance(product, dict) and product.get('name') == name:
                PRODUCTS[category].remove(product)
                deleted = True
                break
        if deleted:
            save_products()
            bot.send_message(message.chat.id, "✅ Товар удалён!", reply_markup=get_admin_reply_menu())
            break


@bot.message_handler(func=lambda message: message.text == "🖼 Приветствие")
def admin_set_welcome_reply(message):
    if not is_admin(message.from_user.id):
        return
    user_data[message.from_user.id] = {'waiting_for': 'welcome_media'}
    bot.send_message(
        message.chat.id,
        "Отправьте фото/видео/гиф с подписью — это будет приветствие."
    )


@bot.message_handler(func=lambda message: message.text == "📊 Статистика")
def admin_stats_reply(message):
    if not is_admin(message.from_user.id):
        return

    # Собираем данные (копируй из admin_stats)
    total_orders = len(ORDERS)
    new_orders = sum(1 for o in ORDERS.values() if o.get('status') == 'new')
    # ... (остальной код)

    bot.send_message(
        message.chat.id,
        f"📊 *Статистика*\nВсего заказов: {total_orders}\nНовых: {new_orders}",
        parse_mode="Markdown",
        reply_markup=get_admin_reply_menu()
    )


@bot.message_handler(func=lambda message: message.text == "🚚 Заказы")
def admin_orders_reply(message):
    if not is_admin(message.from_user.id):
        return

    total_orders = len(ORDERS)
    new_orders = sum(1 for o in ORDERS.values() if o.get('status') == 'new')

    bot.send_message(
        message.chat.id,
        f"🚚 *Заказы*\nВсего: {total_orders}\nНовых: {new_orders}",
        parse_mode="Markdown",
        reply_markup=get_orders_list_menu()  # пока оставим Inline, если не хочешь переделывать
    )

# --- ЛОГИКА ПОДДЕРЖКИ ---

@bot.callback_query_handler(func=lambda c: c.data.startswith("support_take::"))
def support_take_handler(call):
    if not is_admin(call.from_user.id): return
    try:
        ticket_id = int(call.data.split("::")[1])
        ticket = SUPPORT_TICKETS.get(ticket_id)
        
        if not ticket or ticket['status'] != 'new':
            bot.answer_callback_query(call.id, "⚠️ Тикет уже в работе или закрыт.")
            return
        
        ticket['status'] = 'in_work'
        admin_name = call.from_user.first_name or "Админ"
        
        # 1. Обновляем сообщение в группе
        new_text = call.message.text.replace("Статус: *Новый*", f"Статус: *В работе* (Менеджер: {admin_name})")
        bot.edit_message_text(
            new_text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=get_support_admin_menu(ticket_id, 'in_work')
        )
        
        # 2. Уведомляем клиента
        client_text = f"✅ *Тикет #{ticket_id} взят в работу!*\nМенеджер *{admin_name}* скоро свяжется с вами."
        bot.send_message(ticket['user_id'], client_text, parse_mode="Markdown")
        
        bot.answer_callback_query(call.id, f"Тикет #{ticket_id} взят в работу.")
        
    except Exception as e:
        logger.error(f"Ошибка в support_take_handler: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при взятии тикета в работу.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("support_reply::"))
def support_reply_admin_handler(call):
    if not is_admin(call.from_user.id): return
    try:
        ticket_id = int(call.data.split("::")[1])
        ticket = SUPPORT_TICKETS.get(ticket_id)
        
        if not ticket or ticket['status'] != 'in_work':
            bot.answer_callback_query(call.id, "⚠️ Тикет не в работе.")
            return
        
        user_id = call.from_user.id
        if user_id not in user_data:
            user_data[user_id] = {}
        user_data[user_id]['waiting_for'] = f'msg_to_support::{ticket_id}'
        
        # Отправляем сообщение админу, чтобы он ввел ответ
        bot.send_message(
            call.message.chat.id,
            f"✍️ *Введите ответ для клиента по тикету #{ticket_id}:*",
            parse_mode="Markdown",
            reply_to_message_id=call.message.message_id,
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel"))
        )
        
        bot.answer_callback_query(call.id, "Ожидаю ваш ответ...")
        
    except Exception as e:
        logger.error(f"Ошибка в support_reply_admin_handler: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при подготовке ответа.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("support_close::"))
def support_close_handler(call):
    if not is_admin(call.from_user.id): return
    try:
        ticket_id = int(call.data.split("::")[1])
        ticket = SUPPORT_TICKETS.get(ticket_id)
        
        if not ticket or ticket['status'] == 'closed':
            bot.answer_callback_query(call.id, "⚠️ Тикет уже закрыт.")
            return
        
        ticket['status'] = 'closed'
        
        # 1. Обновляем сообщение в группе
        admin_name = call.from_user.first_name or "Админ"
        new_text = call.message.text.replace("Статус: *В работе*", f"Статус: *Закрыт* (Менеджер: {admin_name})")
        bot.edit_message_text(
            new_text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=None # Убираем кнопки
        )
        
        # 2. Уведомляем клиента и ставим кулдаун
        client_id = ticket['user_id']
        client_text = f"✅ *Тикет #{ticket_id} закрыт!*\nПроблема решена. Вы сможете создать новый тикет через 5 минут."
        bot.send_message(client_id, client_text, parse_mode="Markdown")
        
        # Устанавливаем кулдаун
        if client_id not in user_data:
            user_data[client_id] = {}
        user_data[client_id]['support_cooldown_until'] = datetime.now() + timedelta(seconds=SUPPORT_COOLDOWN_SECONDS)
        
        bot.answer_callback_query(call.id, f"Тикет #{ticket_id} закрыт. Клиенту установлен кулдаун.")
        
    except Exception as e:
        logger.error(f"Ошибка в support_close_handler: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при закрытии тикета.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("client_reply::"))
def client_reply_callback(call):
    try:
        ticket_id = int(call.data.split("::")[1])
        ticket = SUPPORT_TICKETS.get(ticket_id)
        
        if not ticket or ticket['status'] != 'in_work':
            bot.answer_callback_query(call.id, "⚠️ Тикет не активен или закрыт.")
            return
        
        user_id = call.from_user.id
        if user_id not in user_data:
            user_data[user_id] = {}
        
        # Устанавливаем состояние ожидания ответа клиента
        user_data[user_id]['waiting_for'] = 'client_reply_message'
        user_data[user_id]['current_ticket_id'] = ticket_id
        
        # Отправляем сообщение клиенту, чтобы он ввел ответ
        bot.send_message(
            call.message.chat.id,
            f"✍️ *Введите ваше сообщение по тикету #{ticket_id}:*",
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel"))
        )
        
        bot.answer_callback_query(call.id, "Ожидаю ваше сообщение...")
        
    except Exception as e:
        logger.error(f"Ошибка в client_reply_callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при подготовке ответа.")

# --- ОБРАБОТЧИКИ КОМАНД ---

@bot.message_handler(content_types=['photo', 'video', 'animation'])
def universal_photo_handler(message):
    user_id = message.from_user.id
    wf = user_data.get(user_id, {}).get('waiting_for')
    if not wf:
        return

    # Определяем тип и file_id
    if message.content_type == 'photo':
        file_id = message.photo[-1].file_id
        media_type = 'photo'
    elif message.content_type == 'video':
        file_id = message.video.file_id
        media_type = 'video'
    elif message.content_type == 'animation':
        file_id = message.animation.file_id
        media_type = 'animation'
    else:
        return

    # === 1. Приветствие ===
    if wf == "welcome_media":
        caption = message.caption or "🏪 Добро пожаловать в Orphelins Dorés!\nДоставим стиль прямо к вам! 🚀\nВыберите категорию:"
        PRODUCTS["welcome"] = {
            "type": media_type,
            "file_id": file_id,
            "caption": caption
        }
        save_products()
        send_one_msg(
            message.chat.id,
            "✅ Приветствие обновлено!",
            reply_markup=get_admin_reply_menu,
            user_id=user_id
        )
        del user_data[user_id]['waiting_for']

    # === 2. Добавление нового товара ===
    elif wf.startswith('photo_new_'):
        category = user_data[user_id]['new_product']['category']
        
        sizes = user_data[user_id]['new_product']['sizes']
        user_data[user_id]['new_product']['stock'] = {size: True for size in sizes}
        
        user_data[user_id]['new_product']['image'] = file_id

        new_prod = user_data[user_id]['new_product'].copy()
        new_prod['category'] = category
        PRODUCTS[category].append(new_prod)
        save_products()

        send_one_msg(
            message.chat.id,
            f"✅ Товар успешно добавлен!\n\n"
            f"Название: {user_data[user_id]['new_product']['name']}\n"
            f"Цена: {user_data[user_id]['new_product']['price']} ₽",
            reply_markup=get_admin_reply_menu,
            user_id=user_id
        )
        del user_data[user_id]['new_product']
        del user_data[user_id]['waiting_for']

    # === 3. Изменение фото существующего товара ===
    elif wf.startswith('photo_') and not wf.startswith('photo_new_'):
        try:
            product_id = int(wf.split('_')[1])
            updated = False
            for category in ["shoes", "clothes"]:
                for product in PRODUCTS.get(category, []):
                    if isinstance(product, dict) and product.get('id') == product_id:
                        product['image'] = file_id
                        updated = True
                        break
                if updated:
                    save_products()
                    send_one_msg(
                        message.chat.id,
                        "✅ Фото товара обновлено!",
                        reply_markup=get_admin_reply_menu,
                        user_id=user_id
                    )
                    break
            del user_data[user_id]['waiting_for']
        except (ValueError, IndexError):
            bot.send_message(message.chat.id, "❌ Ошибка при обновлении фото.")
            del user_data[user_id]['waiting_for']

# --- ОБРАБОТЧИК ТЕКСТА (ПОСЛЕДНИЙ) ---

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if user_id not in user_data or 'waiting_for' not in user_data[user_id]:
        send_welcome(message.chat.id, user_id)
        return

    waiting_for = user_data[user_id]['waiting_for']
    
    # 1. Обработка ответа админа на тикет поддержки
    if is_admin(user_id) and waiting_for.startswith('msg_to_support::'):
        try:
            ticket_id = int(waiting_for.split("::")[1])
            ticket = SUPPORT_TICKETS.get(ticket_id)
            
            if ticket and ticket['status'] == 'in_work':
                admin_name = message.from_user.first_name or "Менеджер"
                
                client_text = f"💬 *Ответ менеджера {admin_name} по тикету #{ticket_id}:*\n\n{text}"
                
                mk_client = types.InlineKeyboardMarkup()
                mk_client.add(types.InlineKeyboardButton("✉️ Ответить менеджеру", callback_data=f"client_reply::{ticket_id}"))
                
                bot.send_message(ticket['user_id'], client_text, parse_mode="Markdown", reply_markup=mk_client)
                
                bot.reply_to(message, f"✅ Ответ по тикету #{ticket_id} отправлен клиенту.")
                
                try:
                    bot.edit_message_reply_markup(
                        message.chat.id,
                        ticket['admin_msg_id'],
                        reply_markup=get_support_admin_menu(ticket_id, 'in_work')
                    )
                except Exception as e:
                    logger.warning(f"Не удалось обновить разметку сообщения тикета: {e}")
                
            else:
                bot.reply_to(message, "❌ Тикет не найден или не в работе.")
                
            del user_data[user_id]['waiting_for']
            return
        except Exception as e:
            logger.error(f"Ошибка при отправке ответа админа: {e}")
            bot.reply_to(message, f"❌ Критическая ошибка при отправке ответа: {e}")
            del user_data[user_id]['waiting_for']
            return

    # 2. Обработка сообщения клиента в ответ на тикет
    if waiting_for == 'client_reply_message':
        try:
            ticket_id = user_data[user_id]['current_ticket_id']
            ticket = SUPPORT_TICKETS.get(ticket_id)
            
            if ticket and ticket['status'] == 'in_work':
                client_name = message.from_user.first_name or "Клиент"
                
                admin_text = f"💬 *Новое сообщение от клиента по тикету #{ticket_id}*\n👤 {client_name}\n\n{text}"
                
                bot.send_message(
                    SUPPORT_GROUP_ID,
                    admin_text,
                    parse_mode="Markdown",
                    reply_markup=get_support_admin_menu(ticket_id, 'in_work')
                )
                
                bot.reply_to(message, "✅ Ваше сообщение отправлено менеджеру.")
            else:
                bot.reply_to(message, "⚠️ Ваш тикет не активен. Создайте новый через меню 'Поддержка'.")
                
            del user_data[user_id]['waiting_for']
            del user_data[user_id]['current_ticket_id']
            return
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения клиента: {e}")
            bot.reply_to(message, f"❌ Критическая ошибка при отправке сообщения: {e}")
            del user_data[user_id]['waiting_for']
            del user_data[user_id]['current_ticket_id']
            return

    # 3. Обработка создания нового тикета
    if waiting_for == 'support_message':
        try:
            global NEXT_TICKET_ID
            ticket_id = NEXT_TICKET_ID
            NEXT_TICKET_ID += 1
            
            username, full_name = get_user_info(message.from_user)
            
            ticket_data = {
                "id": ticket_id,
                "user_id": user_id,
                "username": username,
                "full_name": full_name,
                "message": text,
                "status": "new",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            SUPPORT_TICKETS[ticket_id] = ticket_data
            
            admin_text = (
                f"🆘 *Новый запрос в поддержку #{ticket_id}*\n"
                f"👤 {full_name}\n"
                f"📱 {username}\n"
                f"🆔 `{user_id}`\n"
                f"Статус: *Новый*\n\n"
                f"**Сообщение:**\n{text}"
            )
            
            mk_admin = get_support_admin_menu(ticket_id, 'new')
            
            admin_msg = bot.send_message(
                SUPPORT_GROUP_ID,
                admin_text,
                parse_mode="Markdown",
                reply_markup=mk_admin
            )
            
            ticket_data['admin_msg_id'] = admin_msg.message_id
            
            client_text = f"✅ *Ваш запрос #{ticket_id} принят!*\nМенеджер скоро ответит вам."
            send_one_msg(message.chat.id, client_text, parse_mode="Markdown", user_id=user_id)
            
            del user_data[user_id]['waiting_for']
            return
        except Exception as e:
            logger.error(f"Ошибка при создании тикета: {e}")
            send_one_msg(message.chat.id, "❌ Произошла ошибка при создании тикета. Попробуйте позже.", user_id=user_id)
            if user_id in user_data:
                del user_data[user_id]['waiting_for']
            return

    # 4. Обработка добавления/редактирования товаров
    
    # === Добавление нового товара ===
    if waiting_for.startswith('name_new_'):
        category = waiting_for.split('_')[2]
        if category not in ["shoes", "clothes"]:
            bot.send_message(message.chat.id, "❌ Недопустимая категория.")
            return
        user_data[user_id]['new_product'] = {
            'id': get_next_product_id(category),
            'name': text,
            'price': 0,
            'sizes': [],
            'image': None,
            'category': category
        }
        user_data[user_id]['waiting_for'] = f'price_new_{category}'
        send_one_msg(message.chat.id, "💰 Введите цену товара (только число):", user_id=user_id)

    elif waiting_for.startswith('price_new_'):
        category = waiting_for.split('_')[2]
        try:
            price = int(text)
            user_data[user_id]['new_product']['price'] = price
            user_data[user_id]['waiting_for'] = f'sizes_new_{category}'
            bot.send_message(
                message.chat.id,
                "📏 Введите размеры через запятую.\n\n"
                "Например: 36, 37, 38, 39, 40\n"
                "Или для одежды: S, M, L, XL"
            )
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка! Введите корректную цену (только цифры):")

    elif waiting_for.startswith('sizes_new_'):
        category = waiting_for.split('_')[2]
        sizes = [s.strip() for s in text.split(',') if s.strip()]
        if not sizes:
            bot.send_message(message.chat.id, "❌ Размеры не могут быть пустыми. Повторите ввод:")
            return
        user_data[user_id]['new_product']['sizes'] = sizes
        user_data[user_id]['waiting_for'] = f'photo_new_{category}'
        bot.send_message(
            message.chat.id,
            "🖼 Отправьте фото товара:"
        )
    # === Редактирование существующего товара ===
    elif waiting_for.startswith('name_'):
        try:
            product_id = int(waiting_for.split('_')[1])
            updated = False
            for category in ["shoes", "clothes"]:
                for product in PRODUCTS.get(category, []):
                    if isinstance(product, dict) and product.get('id') == product_id:
                        product['name'] = text
                        updated = True
                        break
                if updated:
                    save_products()
                    bot.send_message(message.chat.id, "✅ Название обновлено!", reply_markup=get_admin_reply_menu)
                    break
            else:
                bot.send_message(message.chat.id, "❌ Товар не найден.")
        except Exception as e:
            logger.error(f"Ошибка при изменении названия: {e}")
            bot.send_message(message.chat.id, "❌ Ошибка при обновлении названия.")
        if user_id in user_data:
            user_data[user_id].pop('waiting_for', None)

    elif waiting_for.startswith('price_'):
        try:
            product_id = int(waiting_for.split('_')[1])
            price = int(text)
            updated = False
            for category in ["shoes", "clothes"]:
                for product in PRODUCTS.get(category, []):
                    if isinstance(product, dict) and product.get('id') == product_id:
                        product['price'] = price
                        updated = True
                        break
                if updated:
                    save_products()
                    bot.send_message(message.chat.id, "✅ Цена обновлена!", reply_markup=get_admin_reply_menu)
                    break
            else:
                bot.send_message(message.chat.id, "❌ Товар не найден.")
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка! Введите корректную цену (только цифры).")
        except Exception as e:
            logger.error(f"Ошибка при изменении цены: {e}")
            bot.send_message(message.chat.id, "❌ Ошибка при обновлении цены.")
        if user_id in user_data:
            user_data[user_id].pop('waiting_for', None)

    elif waiting_for.startswith('sizes_'):
        try:
            product_id = int(waiting_for.split('_')[1])
            sizes = [s.strip() for s in text.split(',') if s.strip()]
            if not sizes:
                bot.send_message(message.chat.id, "❌ Размеры не могут быть пустыми. Повторите ввод:")
                return
            updated = False
            for category in ["shoes", "clothes"]:
                for product in PRODUCTS.get(category, []):
                    if isinstance(product, dict) and product.get('id') == product_id:
                        product['sizes'] = sizes
                        updated = True
                        break
                if updated:
                    save_products()
                    send_one_msg(message.chat.id, "✅ Размеры обновлены!", reply_markup=get_admin_reply_menu, user_id=user_id)
                    break
            else:
                bot.send_message(message.chat.id, "❌ Товар не найден.")
        except Exception as e:
            logger.error(f"Ошибка при изменении размеров: {e}")
            bot.send_message(message.chat.id, "❌ Ошибка при обновлении размеров.")
        if user_id in user_data:
            user_data[user_id].pop('waiting_for', None)
        
    # 5. Обработка ответа админа на заказ (старая логика)
    elif waiting_for.startswith('msg_to_'):
        try:
            client_user_id = int(waiting_for.split('_')[2])
            bot.send_message(
                client_user_id,
                f"💬 Сообщение от менеджера:\n\n{message.text}"
            )
            bot.reply_to(message, "✅ Сообщение отправлено клиенту в ЛС")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {str(e)}")
        
        del user_data[user_id]['waiting_for']
        return
        
    return

# --- ЗАПУСК ---

def check_bot_in_group():
    try:
        chat_info = bot.get_chat(ADMIN_GROUP_ID)
        bot_info = bot.get_me()
        member = bot.get_chat_member(ADMIN_GROUP_ID, bot_info.id)
        return member.status in ['administrator', 'member']
    except Exception as e:
        logger.error(f"Ошибка доступа к основной админ-группе: {e}")
        return False
def convert_old_products():
    updated = False
    for category in ["shoes", "clothes"]:
        for product in PRODUCTS.get(category, []):
            if 'sizes' in product and 'stock' not in product:
                product['stock'] = {size: True for size in product['sizes']}
                updated = True
    if updated:
        save_products()
        print("✅ Товары обновлены — добавлено поле 'stock'")

def check_bot_in_support_group():
    try:
        chat_info = bot.get_chat(SUPPORT_GROUP_ID)
        bot_info = bot.get_me()
        member = bot.get_chat_member(SUPPORT_GROUP_ID, bot_info.id)
        return member.status in ['administrator', 'member']
    except Exception as e:
        logger.error(f"Ошибка доступа к группе поддержки: {e}")
        return False

if __name__ == "__main__":
    try:
        load_products()
        convert_old_products()  # Запустится один раз
        load_orders()
        print("✅ Товары и заказы загружены. Бот запускается...")
        
        if check_bot_in_group():
            print("✅ Доступ к основной админ-группе подтвержден")
        else:
            print("⚠️ Проблемы с основной группой. Проверьте ID и права бота.")

        if check_bot_in_support_group():
            print("✅ Доступ к группе поддержки подтвержден")
        else:
            print("⚠️ Проблемы с группой поддержки. Проверьте ID и права бота.")

        bot.infinity_polling()
    except Exception as e:
        print("❌ КРИТИЧЕСКАЯ ОШИБКА:")
        print(e)
