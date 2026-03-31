import asyncio
import logging
import os
import aiosqlite
import re
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile

# ===================== НАСТРОЙКИ =====================
BOT_TOKEN = "8608901091:AAEs3oIRYdRQ-gyBD3AdWPVQDAEBoe3Fj20"
ADMIN_IDS = [1740905643, 8568355956, 1049417228, 914617147]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "quiet_corner.db")
DIR_COFFEE = os.path.join(BASE_DIR, "images", "coffee")
DIR_DESSERTS = os.path.join(BASE_DIR, "images", "desserts")
IMG_WELCOME = os.path.join(BASE_DIR, "images", "welcome.jpg")

logging.basicConfig(level=logging.INFO)

# ===================== МЕНЮ И ЦЕНЫ =====================
MENU_ITEMS = {
    "Капучино": {"price": 220, "file": "cappuccino.jpg", "type": "coffee"},
    "Латте": {"price": 250, "file": "latte.jpg", "type": "coffee"},
    "Раф Лаванда": {"price": 290, "file": "raf.jpg", "type": "coffee"},
    "Чизкейк": {"price": 350, "file": "cheesecake.jpg", "type": "dessert"},
    "Круассан": {"price": 180, "file": "croissant.jpg", "type": "dessert"}
}

TIME_SLOTS = [f"{h:02d}:00" for h in range(8, 21)]

# ===================== БАЗА ДАННЫХ =====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            cups_count INTEGER DEFAULT 0)""")

        await db.execute("""CREATE TABLE IF NOT EXISTS tables (
            id INTEGER PRIMARY KEY,
            name TEXT)""")

        # Новая таблица для бронирования по времени
        await db.execute("""CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            table_id INTEGER,
            time_slot TEXT,
            status TEXT DEFAULT 'pending'
            -- status: pending (ожидает админа), confirmed (подтверждено)
        )""")

        async with db.execute("SELECT COUNT(*) FROM tables") as cur:
            if (await cur.fetchone())[0] == 0:
                for i in range(1, 6):
                    await db.execute("INSERT INTO tables (name) VALUES (?)", (f"Столик №{i}",))
        await db.commit()
    print(f"✅ База данных готова: {DB_PATH}")

class OrderStates(StatesGroup):
    waiting_phone = State()
    waiting_name = State()
    waiting_address = State()
    waiting_time = State()

class CoffeeStates(StatesGroup):
    add_cup_uid = State()

# ===================== КЛАВИАТУРЫ =====================
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☕️ Кофе", callback_data="menu_coffee"),
         InlineKeyboardButton(text="🍰 Десерты", callback_data="menu_desserts")],
        [InlineKeyboardButton(text="🛒 Моя корзина", callback_data="view_cart")],
        [InlineKeyboardButton(text="🪑 Забронировать столик", callback_data="book_table")],
        [InlineKeyboardButton(text="🎁 Мои бонусы", callback_data="my_bonuses")]
    ])

def back_to_main():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ В главное меню", callback_data="start_over")]])

def order_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏃‍♂️ Самовывоз", callback_data="order_pickup")],
        [InlineKeyboardButton(text="🚗 Доставка", callback_data="order_delivery")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="view_cart")]
    ])

def admin_confirm_kb(user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"adm_ok_{user_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_no_{user_id}")]
    ])

# ===================== ЛОГИКА БОТА =====================
dp = Dispatcher(storage=MemoryStorage())

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.set_state(None)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
                         (message.from_user.id, message.from_user.username))
        await db.commit()

    welcome_text = "<b>Добро пожаловать в «Тихий уголок»!</b> ☕️🌿\n\nСделайте заказ или забронируйте столик:"

    if os.path.exists(IMG_WELCOME):
        await message.answer_photo(photo=FSInputFile(IMG_WELCOME), caption=welcome_text, reply_markup=main_menu())
    else:
        await message.answer(welcome_text, reply_markup=main_menu())

@dp.callback_query(F.data == "start_over")
async def cb_start_over(callback: CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await callback.message.delete()
    await cmd_start(callback.message, state)
    await callback.answer()

# --- КАТАЛОГ ТОВАРОВ И КОРЗИНА ---
@dp.callback_query(F.data.in_(["menu_coffee", "menu_desserts"]))
async def show_menu(callback: CallbackQuery):
    is_coffee = callback.data == "menu_coffee"
    target_type = "coffee" if is_coffee else "dessert"
    folder = DIR_COFFEE if is_coffee else DIR_DESSERTS

    await callback.message.delete()

    for name, info in MENU_ITEMS.items():
        if info["type"] == target_type:
            path = os.path.join(folder, info["file"])
            if os.path.exists(path):
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"➕ В корзину ({info['price']}₽)", callback_data=f"add_{name}")]
                ])
                await callback.message.answer_photo(FSInputFile(path), caption=f"<b>{name}</b>", reply_markup=kb)

    await callback.message.answer("<i>Выберите позицию:</i>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Перейти в корзину", callback_data="view_cart")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_over")]
    ]))
    await callback.answer()

@dp.callback_query(F.data.startswith("add_"))
async def add_to_cart(callback: CallbackQuery, state: FSMContext):
    item_name = callback.data.split("_", 1)[1]
    data = await state.get_data()
    cart = data.get("cart", {})
    cart[item_name] = cart.get(item_name, 0) + 1
    await state.update_data(cart=cart)
    await callback.answer(f"✅ {item_name} добавлен в корзину!", show_alert=False)

@dp.callback_query(F.data == "view_cart")
async def view_cart(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart", {})
    await callback.message.delete()
    if not cart:
        await callback.message.answer("🛒 <b>Ваша корзина пуста</b>\nЗагляните в меню!", reply_markup=back_to_main())
        return
    text = "🛒 <b>Ваша корзина:</b>\n\n"
    total_sum = 0
    kb = []
    for item_name, count in cart.items():
        price = MENU_ITEMS[item_name]["price"]
        item_sum = price * count
        total_sum += item_sum
        text += f"▪️ <b>{item_name}</b> x{count} = {item_sum}₽\n"

        kb.append([
            InlineKeyboardButton(text="➖", callback_data=f"c_dec_{item_name}"),
            InlineKeyboardButton(text=f"{item_name}", callback_data="ignore"),
            InlineKeyboardButton(text="➕", callback_data=f"c_inc_{item_name}")
        ])
    text += f"\n💰 <b>Итого к оплате: {total_sum}₽</b>"
    kb.append([InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="cart_clear")])
    kb.append([InlineKeyboardButton(text="✅ Оформить заказ", callback_data="cart_checkout")])
    kb.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="start_over")])
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@dp.callback_query(F.data.startswith("c_inc_"))
async def cart_inc(callback: CallbackQuery, state: FSMContext):
    item_name = callback.data.split("_", 2)[2]
    data = await state.get_data()
    cart = data.get("cart", {})
    if item_name in cart:
        cart[item_name] += 1
        await state.update_data(cart=cart)
    await view_cart(callback, state)

@dp.callback_query(F.data.startswith("c_dec_"))
async def cart_dec(callback: CallbackQuery, state: FSMContext):
    item_name = callback.data.split("_", 2)[2]
    data = await state.get_data()
    cart = data.get("cart", {})
    if item_name in cart:
        cart[item_name] -= 1
        if cart[item_name] <= 0:
            del cart[item_name]
        await state.update_data(cart=cart)
    await view_cart(callback, state)

@dp.callback_query(F.data == "cart_clear")
async def cart_clear(callback: CallbackQuery, state: FSMContext):
    await state.update_data(cart={})
    await callback.answer("🗑 Корзина очищена")
    await view_cart(callback, state)

# --- ОФОРМЛЕНИЕ ЗАКАЗА ---
@dp.callback_query(F.data == "cart_checkout")
async def cart_checkout(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📍 <b>Оформление заказа</b>\n\nВыберите способ получения:", reply_markup=order_type_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("order_"))
async def choose_order_type(callback: CallbackQuery, state: FSMContext):
    o_type = "Самовывоз" if "pickup" in callback.data else "Доставка"
    await state.update_data(order_type=o_type)

    await callback.message.edit_text(
        f"Вы выбрали: <b>{o_type}</b>\n\n"
        f"📞 Пожалуйста, отправьте ваш <b>номер телефона</b> (например: +79991234567):"
    )
    await state.set_state(OrderStates.waiting_phone)
    await callback.answer()

@dp.message(OrderStates.waiting_phone)
async def process_phone(message: types.Message, state: FSMContext):
    if not re.match(r'^\+?[\d\s\-\(\)]{10,20}$', message.text):
        await message.answer("⚠️ Пожалуйста, введите <b>только корректный номер телефона</b> без лишних слов и букв:")
        return

    await state.update_data(phone=message.text)

    await message.answer("👤 Как к вам обращаться? Введите ваше <b>имя</b>:")
    await state.set_state(OrderStates.waiting_name)

@dp.message(OrderStates.waiting_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    data = await state.get_data()

    if data.get("order_type") == "Самовывоз":
        await message.answer("⏰ Укажите примерное <b>время</b>, когда заберете заказ (например: 14:30):")
        await state.set_state(OrderStates.waiting_time)
    else:
        await message.answer("📍 Укажите точный <b>адрес доставки</b> (улица, дом, квартира/офис, подъезд):")
        await state.set_state(OrderStates.waiting_address)

@dp.message(OrderStates.waiting_time)
async def process_time(message: types.Message, state: FSMContext, bot: Bot):
    await state.update_data(extra_info=f"⏰ <b>Время самовывоза:</b> {message.text}")
    await send_order_to_admins(message, state, bot)

@dp.message(OrderStates.waiting_address)
async def process_address(message: types.Message, state: FSMContext, bot: Bot):
    await state.update_data(extra_info=f"📍 <b>Адрес доставки:</b> {message.text}")
    await send_order_to_admins(message, state, bot)

async def send_order_to_admins(message: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    cart = data.get("cart", {})
    o_type = data.get("order_type")
    phone = data.get("phone")
    name = data.get("name")
    extra_info = data.get("extra_info")

    if not cart:
        await message.answer("Ваша корзина пуста.", reply_markup=back_to_main())
        await state.set_state(None)
        return

    order_list, total_sum = "", 0
    for item, count in cart.items():
        price = MENU_ITEMS[item]["price"]
        order_list += f"▫️ {item} x{count} = {price * count}₽\n"
        total_sum += price * count

    await message.answer("✅ <b>Ваш заказ успешно отправлен!</b>\nАдминистратор проверит его и пришлет подтверждение.", reply_markup=main_menu())

    username = f"@{message.from_user.username}" if message.from_user.username else "Скрыт"

    admin_text = (f"🔔 <b>НОВЫЙ ЗАКАЗ!</b>\n\n"
                  f"📦 Тип: <b>{o_type}</b>\n"
                  f"👤 Клиент: {name} ({username})\n"
                  f"📞 Телефон: <code>{phone}</code>\n"
                  f"{extra_info}\n\n"
                  f"🛒 <b>Состав заказа:</b>\n{order_list}\n"
                  f"💰 <b>Сумма: {total_sum}₽</b>")

    for adm in ADMIN_IDS:
        await bot.send_message(adm, admin_text, reply_markup=admin_confirm_kb(message.from_user.id))

    await state.update_data(cart={})
    await state.set_state(None)

# --- НОВОЕ БРОНИРОВАНИЕ (ВЫБОР СТОЛИКА И ВРЕМЕНИ) ---

@dp.callback_query(F.data == "book_table")
async def book_table_list(callback: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name FROM tables") as cur:
            tables = await cur.fetchall()

    keyboard = []
    for t_id, t_name in tables:
        keyboard.append([InlineKeyboardButton(text=f"🪑 {t_name}", callback_data=f"sel_table_{t_id}")])

    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="start_over")])

    await callback.message.delete()
    await callback.message.answer("<b>Выберите столик:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

@dp.callback_query(F.data.startswith("sel_table_"))
async def select_time_slot(callback: CallbackQuery):
    table_id = int(callback.data.split("_")[2])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT time_slot, status FROM reservations WHERE table_id = ?", (table_id,)) as cur:
            reservations = await cur.fetchall()

    occupied_slots = {res[0]: res[1] for res in reservations}

    keyboard = []
    row = []
    for time in TIME_SLOTS:
        if time in occupied_slots:
            if occupied_slots[time] == "confirmed":
                btn_text = f"❌ {time}"
                cb_data = "ignore_busy"
            else:
                btn_text = f"⏳ {time}"
                cb_data = "ignore_pending"
        else:
            btn_text = f"✅ {time}"
            cb_data = f"book_time_{table_id}_{time}"

        row.append(InlineKeyboardButton(text=btn_text, callback_data=cb_data))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton(text="⬅️ К выбору стола", callback_data="book_table")])
    await callback.message.edit_text(f"<b>Столик №{table_id}</b>\nВыберите свободное время:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()

@dp.callback_query(F.data.in_(["ignore_busy", "ignore_pending"]))
async def ignore_busy_clicks(callback: CallbackQuery):
    msg = "Этот столик уже занят!" if callback.data == "ignore_busy" else "Этот слот сейчас рассматривается администратором!"
    await callback.answer(msg, show_alert=True)

@dp.callback_query(F.data.startswith("book_time_"))
async def process_reserve_time(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split("_")
    table_id = parts[2]
    time_slot = parts[3]
    user_id = callback.from_user.id
    username = callback.from_user.username or "Без имени"

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO reservations (user_id, table_id, time_slot, status) VALUES (?, ?, ?, 'pending')",
                         (user_id, table_id, time_slot))
        res_id = cur.lastrowid
        await db.commit()

    await callback.message.edit_text(f"⏳ <b>Заявка на бронь отправлена!</b>\nВы выбрали Столик №{table_id} на {time_slot}.\nОжидайте подтверждения от администратора.", reply_markup=back_to_main())
    await callback.answer()

    admin_text = f"🛎 <b>НОВАЯ ЗАЯВКА НА БРОНЬ!</b>\n\n👤 Юзер: @{username} (ID: {user_id})\n🪑 Столик: №{table_id}\n⏰ Время: {time_slot}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"resok_{res_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"resno_{res_id}")]
    ])

    for adm in ADMIN_IDS:
        await bot.send_message(adm, admin_text, reply_markup=kb)


# --- ПОДТВЕРЖДЕНИЕ БРОНИ АДМИНОМ ---
@dp.callback_query(F.data.startswith("resok_") | F.data.startswith("resno_"))
async def admin_res_decision(callback: CallbackQuery, bot: Bot):
    action = callback.data.split("_")[0]
    res_id = int(callback.data.split("_")[1])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, table_id, time_slot, status FROM reservations WHERE id = ?", (res_id,)) as cur:
            res = await cur.fetchone()

        if not res:
            await callback.answer("Заявка не найдена!")
            return

        user_id, table_id, time_slot, status = res

        if status != 'pending':
            await callback.answer("Эта заявка уже обработана!")
            return

        if action == "resok":
            await db.execute("UPDATE reservations SET status = 'confirmed' WHERE id = ?", (res_id,))
            await bot.send_message(user_id, f"✅ <b>Ваша бронь подтверждена!</b>\nЖдем вас за Столиком №{table_id} в {time_slot}.")
            await callback.message.edit_text(callback.message.text + "\n\n✅ <b>ПОДТВЕРЖДЕНА</b>")
        else:
            await db.execute("DELETE FROM reservations WHERE id = ?", (res_id,))
            await bot.send_message(user_id, f"❌ <b>Бронь отклонена.</b>\nК сожалению, Столик №{table_id} на {time_slot} забронировать не удалось.")
            await callback.message.edit_text(callback.message.text + "\n\n❌ <b>ОТКЛОНЕНА</b>")
        await db.commit()
    await callback.answer()

# --- БОНУСЫ ---
@dp.callback_query(F.data == "my_bonuses")
async def show_bonuses(callback: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT cups_count FROM users WHERE user_id = ?", (callback.from_user.id,)) as cur:
            row = await cur.fetchone()
            cups = row[0] if row else 0
    progress = "☕️" * (cups % 6) + "⚪️" * (6 - (cups % 6))
    await callback.message.delete()
    await callback.message.answer(f"<b>Бонусы:</b>\nID: <code>{callback.from_user.id}</code>\nЧашек: {cups}\n{progress}", reply_markup=back_to_main())
    await callback.answer()

# --- АДМИНКА ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статус и отмена броней", callback_data="admin_tables")],
            [InlineKeyboardButton(text="➕ Начислить чашку", callback_data="admin_add_cup")]
        ])
        await message.answer("🛠 <b>Админка</b>", reply_markup=kb)

@dp.callback_query(F.data == "admin_tables")
async def admin_view_tables(callback: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, table_id, time_slot, user_id FROM reservations WHERE status = 'confirmed' ORDER BY table_id, time_slot") as cur:
            reservations = await cur.fetchall()

    if not reservations:
        await callback.message.answer("🟢 На данный момент нет подтвержденных броней.")
        await callback.answer()
        return

    text = "<b>Активные брони:</b>\n\n"
    kb = []

    for res_id, t_id, time_slot, u_id in reservations:
        text += f"🪑 Столик №{t_id} | ⏰ {time_slot} | 👤 ID: <code>{u_id}</code>\n"
        kb.append([InlineKeyboardButton(text=f"❌ Снять бронь: Столик {t_id} ({time_slot})", callback_data=f"adm_delres_{res_id}")])

    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@dp.callback_query(F.data.startswith("adm_delres_"))
async def admin_delete_res(callback: CallbackQuery, bot: Bot):
    res_id = int(callback.data.split("_")[2])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, table_id, time_slot FROM reservations WHERE id = ?", (res_id,)) as cur:
            res = await cur.fetchone()

        if res:
            u_id, t_id, t_slot = res
            await db.execute("DELETE FROM reservations WHERE id = ?", (res_id,))
            await db.commit()

            try:
                await bot.send_message(u_id, f"⚠️ <b>Внимание!</b> Ваша бронь (Столик №{t_id} на {t_slot}) была отменена администратором.")
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление {u_id}: {e}")

            await callback.answer("Бронь успешно удалена!", show_alert=True)
            await callback.message.delete()
        else:
            await callback.answer("Бронь уже удалена или не найдена.")

@dp.callback_query(F.data == "admin_add_cup")
async def admin_cup_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите ID пользователя:")
    await state.set_state(CoffeeStates.add_cup_uid)
    await callback.answer()

@dp.message(CoffeeStates.add_cup_uid)
async def process_add_cup(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET cups_count = cups_count + 1 WHERE user_id = ?", (uid,))
            await db.commit()
        await message.answer(f"✅ Чашка добавлена юзеру {uid}")
    except:
        await message.answer("Ошибка в ID")
    await state.set_state(None)

# --- ОТВЕТ АДМИНА НА ЗАКАЗ ---
@dp.callback_query(F.data.startswith("adm_") & ~F.data.startswith("adm_delres_"))
async def admin_decision(callback: CallbackQuery, bot: Bot):
    action, user_id = callback.data.split("_")[1], int(callback.data.split("_")[2])
    if action == "ok":
        await bot.send_message(user_id, "❤️ <b>Ваш заказ принят!</b> Ожидайте готовности.")
        await callback.message.edit_text(callback.message.text + "\n\n✅ <b>ПРИНЯТ</b>")
    else:
        await bot.send_message(user_id, "❌ <b>Заказ отклонен.</b> Извините за неудобства.")
        await callback.message.edit_text(callback.message.text + "\n\n❌ <b>ОТКЛОНЕН</b>")
    await callback.answer()

# ===================== ЗАПУСК =====================
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот выключен")
