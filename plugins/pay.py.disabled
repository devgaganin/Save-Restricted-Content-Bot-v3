# Copyright (c) 2025 devgagan : https://github.com/devgaganin.  
# Licensed under the GNU General Public License v3.0.  
# See LICENSE file in the repository root for full license text.

from pyrogram import filters as f
from shared_client import app
from pyrogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M, LabeledPrice as P, PreCheckoutQuery as Q
from datetime import timedelta as T
from utils.func import add_premium_user as apu
from config import P0

@app.on_message(f.command("pay") & f.private)
async def p(c, m):
    kb = M([
        [
            B(f"⭐ {P0['d']['l']} - {P0['d']['s']} Star", callback_data="p_d")
        ],
        [
            B(f"⭐ {P0['w']['l']} - {P0['w']['s']} Stars", callback_data="p_w")
        ],
        [
            B(f"⭐ {P0['m']['l']} - {P0['m']['s']} Stars", callback_data="p_m")
        ]
    ])
    
    txt = (
        "💎 **Choose your premium plan:**\n\n"
        f"📅 **{P0['d']['l']}** — {P0['d']['s']} Star\n"
        f"🗓️ **{P0['w']['l']}** — {P0['w']['s']} Stars\n"
        f"📆 **{P0['m']['l']}** — {P0['m']['s']} Stars\n\n"
        "Select a plan below to continue ⤵️"
    )
    await m.reply_text(txt, reply_markup=kb)
    
@app.on_callback_query(f.regex("^p_"))
async def i(c, q):
    if not q.from_user:
        await q.answer("Error: Invalid user", show_alert=True)
        return
        
    pl = q.data.split("_")[1]
    pi = P0[pl]
    try:
        await c.send_invoice(
            chat_id=q.from_user.id,
            title=f"Premium {pi['l']}",
            description=f"{pi['du']} {pi['u']} subscription",
            payload=f"{pl}_{q.from_user.id}",
            currency="XTR",
            prices=[P(label=f"Premium {pi['l']}", amount=pi['s'])]
        )
        await q.answer("Invoice sent 💫")
    except Exception as e:
        await q.answer(f"Err: {e}", show_alert=True)

@app.on_pre_checkout_query()
async def pc(c, q: Q): 
    await q.answer(ok=True)

@app.on_message(f.successful_payment)
async def sp(c, m):
    from config import OWNER_ID
    p = m.successful_payment
    u = m.from_user.id if m.from_user else 0
    pl = p.invoice_payload.split("_")[0]
    pi = P0[pl]
    ok, r = await apu(u, pi['du'], pi['u'])
    if ok:
        e = r + T(hours=5, minutes=30)
        d = e.strftime('%d-%b-%Y %I:%M:%S %p')
        await m.reply_text(
            f"✅ **Paid!**\n\n"
            f"💎 Premium {pi['l']} active!\n"
            f"⭐ {p.total_amount}\n"
            f"⏰ Till: {d} IST\n"
            f"🔖 Txn: `{p.telegram_payment_charge_id}`"
        )
        if OWNER_ID:
            for o in OWNER_ID:
                try:
                    await c.send_message(o, f"User {u} just purchased the premium, txn id is {p.telegram_payment_charge_id}.")
                except Exception:
                    pass
    else:
        await m.reply_text(
            f"⚠️ Paid but premium failed.\nTxn `{p.telegram_payment_charge_id}`"
        )
        if OWNER_ID:
            for o in OWNER_ID:
                try:
                    await c.send_message(o,
                        f"⚠️ Issue!\nUser {u}\nPlan {pi['l']}\nTxn {p.telegram_payment_charge_id}\nErr {r}"
                    )
                except Exception:
                    pass
