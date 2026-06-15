"""Static store rendering engine — generates complete static e-commerce sites."""
import json
import os
import logging

import hashlib
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default design system — used when a brand_kit has no design_system
# ---------------------------------------------------------------------------
DEFAULT_DESIGN = {
    "version": 1,
    "layout": {
        "hero": {"type": "gradient", "headline": "Welcome", "subheadline": "Discover our collection"},
        "nav": {"style": "sticky_top", "search": True, "account_icon": True},
        "footer": {"columns": 4, "newsletter": False},
    },
    "product_card": {
        "style": "shadow", "hover_effect": "lift",
        "columns_desktop": 4, "show_rating": True, "show_badge": True,
    },
    "product_detail": {
        "gallery": "thumbnails_left", "tabs": True, "sticky_atc": True,
        "show_breadcrumb": True, "show_sku": True, "show_share": True,
    },
    "cart": {"style": "drawer", "position": "right", "show_related": True},
    "checkout": {"steps": ["shipping", "payment", "review"], "show_coupon": False, "show_order_summary": True},
    "components": ["badge", "breadcrumb", "faq", "related_products", "reviews", "trust_badges"],
    "css_vars": {"--radius": "8px", "--shadow": "0 4px 24px rgba(0,0,0,0.08)", "--transition": "0.3s ease"},
    "typography_scale": 1.0,
    "animation_level": "subtle",
}

# ---------------------------------------------------------------------------
# Single-file JS for store interactivity (cart, checkout, toasts)
# ---------------------------------------------------------------------------
STORE_JS = r"""(function(){
'use strict';
function getCart(){
  try{return JSON.parse(localStorage.getItem('store_cart')||'[]');}catch(e){return[];}
}
function setCart(c){localStorage.setItem('store_cart',JSON.stringify(c));}
function saveOrder(order){localStorage.setItem('store_last_order',JSON.stringify(order));}
function updateCartCount(){
  var c=getCart(),cnt=c.reduce(function(s,i){return s+(i.qty||1);},0);
  var el=document.getElementById('cart-count');
  if(el){el.textContent=cnt;el.style.display=cnt?'inline-block':'none';}
}
function showToast(msg,type){
  type=type||'success';
  var t=document.createElement('div');
  t.className='toast toast-'+type;
  t.textContent=msg;
  t.setAttribute('role','status');
  document.body.appendChild(t);
  requestAnimationFrame(function(){t.classList.add('show');});
  setTimeout(function(){t.classList.remove('show');setTimeout(function(){if(t.parentNode)t.parentNode.removeChild(t);},400);},2500);
}
function addToCart(product){
  var cart=getCart(),found=cart.find(function(i){return i.id===product.id;});
  if(found){found.qty=(found.qty||1)+1;}else{cart.push({id:product.id,title:product.title,price:product.price,image:product.image,qty:1,currency:product.currency||'USD'});}
  setCart(cart);updateCartCount();showToast('Added to cart!');
  var drawer=document.getElementById('cart-drawer');
  if(drawer&&drawer.classList.contains('open')){renderCartDrawer();}
}
function removeFromCart(id){
  var cart=getCart().filter(function(i){return i.id!==id;});
  setCart(cart);updateCartCount();renderCartDrawer();renderCartPage();
}
function updateQty(id,qty){
  var cart=getCart().map(function(i){if(i.id===id){i.qty=Math.max(1,parseInt(qty)||1);}return i;});
  setCart(cart);updateCartCount();renderCartDrawer();renderCartPage();
}
function cartTotal(){return getCart().reduce(function(s,i){return s+(i.price||0)*(i.qty||1);},0);}
function cartCount(){return getCart().reduce(function(s,i){return s+(i.qty||1);},0);}
function renderCartDrawer(){
  var drawer=document.getElementById('cart-drawer'),body=document.getElementById('cart-drawer-body');
  if(!drawer||!body)return;
  var cart=getCart();
  if(!cart.length){body.innerHTML='<p style="text-align:center;color:#999;padding:40px">Your cart is empty</p>';}
  else{
    var html='<ul class="cart-drawer-list">';
    cart.forEach(function(i){
      html+='<li class="cart-drawer-item"><img src="'+(i.image||'')+'" alt="'+i.title+'" onerror="this.style.display=\'none\'"><div class="cart-drawer-info"><strong>'+i.title+'</strong><span>$'+(i.price||0).toFixed(2)+' x '+i.qty+'</span></div><button class="btn-remove" data-id="'+i.id+'" title="Remove">&times;</button></li>';
    });
    html+='</ul><div class="cart-drawer-total"><strong>Total: $'+cartTotal().toFixed(2)+'</strong></div><a href="/cart/" class="btn btn-primary btn-block">View Cart</a>';
    body.innerHTML=html;
    body.querySelectorAll('.btn-remove').forEach(function(btn){btn.addEventListener('click',function(){removeFromCart(btn.getAttribute('data-id'));});});
  }
}
function renderCartPage(){
  var tbody=document.getElementById('cart-table-body'),summary=document.getElementById('cart-summary');
  if(!tbody)return;
  var cart=getCart();
  if(!cart.length){tbody.innerHTML='<tr><td colspan="5" style="text-align:center;padding:40px;color:#999">Your cart is empty.</td></tr>';if(summary)summary.innerHTML='';return;}
  var html='';
  cart.forEach(function(i){
    html+='<tr><td><img src="'+(i.image||'')+'" alt="'+i.title+'" style="width:60px;height:60px;object-fit:cover;border-radius:4px" onerror="this.style.display=\'none\'"></td><td>'+i.title+'</td><td>$'+(i.price||0).toFixed(2)+'</td><td><input type="number" min="1" value="'+i.qty+'" data-id="'+i.id+'" class="qty-input" style="width:60px;padding:4px"></td><td>$'+((i.price||0)*i.qty).toFixed(2)+'</td><td><button class="btn-remove" data-id="'+i.id+'">&times;</button></td></tr>';
  });
  tbody.innerHTML=html;
  if(summary){summary.innerHTML='<p>Subtotal: <strong>$'+cartTotal().toFixed(2)+'</strong></p><p>Items: '+cartCount()+'</p><a href="/checkout/" class="btn btn-primary btn-block">Proceed to Checkout</a>';}
  tbody.querySelectorAll('.qty-input').forEach(function(inp){inp.addEventListener('change',function(){updateQty(inp.getAttribute('data-id'),inp.value);});});
  tbody.querySelectorAll('.btn-remove').forEach(function(btn){btn.addEventListener('click',function(){removeFromCart(btn.getAttribute('data-id'));});});
}
function checkoutNext(step){
  var steps=document.querySelectorAll('.checkout-step');
  steps.forEach(function(s){s.style.display='none';});
  var el=document.getElementById('checkout-step-'+step);
  if(el)el.style.display='block';
  if(step===3){renderReview();}
}
function renderReview(){
  var el=document.getElementById('review-summary'),cart=getCart();
  if(!el)return;
  var html='<table style="width:100%;border-collapse:collapse"><thead><tr><th>Product</th><th>Qty</th><th>Price</th></tr></thead><tbody>';
  cart.forEach(function(i){html+='<tr><td>'+i.title+'</td><td>'+i.qty+'</td><td>$'+((i.price||0)*i.qty).toFixed(2)+'</td></tr>';});
  html+='</tbody></table><p style="text-align:right;font-size:18px;margin-top:16px"><strong>Total: $'+cartTotal().toFixed(2)+'</strong></p>';
  el.innerHTML=html;
}
function placeOrder(){
  var cart=getCart();
  if(!cart.length){showToast('Cart is empty','error');return;}
  var orderId='ORD-'+Date.now()+'-'+Math.random().toString(36).substr(2,6).toUpperCase();
  saveOrder({id:orderId,total:cartTotal().toFixed(2),items:cart.length,date:new Date().toISOString()});
  localStorage.removeItem('store_cart');
  window.location.href='/order/?order='+orderId;
}
document.addEventListener('DOMContentLoaded',function(){
  updateCartCount();
  renderCartDrawer();
  renderCartPage();
  var toggle=document.getElementById('cart-toggle');
  if(toggle){
    toggle.addEventListener('click',function(e){
      e.preventDefault();
      var drawer=document.getElementById('cart-drawer');
      if(drawer){
        renderCartDrawer();
        drawer.classList.toggle('open');
      }
    });
  }
  var close=document.getElementById('cart-close');
  if(close){
    close.addEventListener('click',function(){
      var drawer=document.getElementById('cart-drawer');
      if(drawer)drawer.classList.remove('open');
    });
  }
  document.addEventListener('click',function(e){
    if(e.target.closest('.add-to-cart-btn')){
      var btn=e.target.closest('.add-to-cart-btn');
      var product={id:btn.getAttribute('data-id'),title:btn.getAttribute('data-title'),price:parseFloat(btn.getAttribute('data-price')||0),image:btn.getAttribute('data-image')||''};
      addToCart(product);
    }
  });
  var next1=document.getElementById('btn-step1');
  if(next1)next1.addEventListener('click',function(){checkoutNext(2);});
  var next2=document.getElementById('btn-step2');
  if(next2)next2.addEventListener('click',function(){checkoutNext(3);});
  var place=document.getElementById('btn-place-order');
  if(place)place.addEventListener('click',placeOrder);
  var orderEl=document.getElementById('order-number');
  if(orderEl){
    var params=new URLSearchParams(window.location.search);
    var orderId=params.get('order')||'N/A';
    orderEl.textContent=orderId;
  }
});
})();"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_design(brand_kit):
    """Load and normalize design_system from a brand_kit dict."""
    ds = brand_kit.get("design_system", {})
    if isinstance(ds, str):
        try:
            ds = json.loads(ds)
        except (json.JSONDecodeError, TypeError):
            ds = {}
    if not ds or not ds.get("layout"):
        ds = dict(DEFAULT_DESIGN)
        sr = brand_kit.get("design_system", {})
        if isinstance(sr, str):
            try: sr = json.loads(sr)
            except: sr = {}
        if sr.get("style_recipe"):
            ds["style_recipe"] = sr["style_recipe"]
    return ds
    return ds


def _brand_colors(brand_kit):
    """Extract brand color array, falling back to sensible defaults."""
    colors = brand_kit.get("colors", [])
    if isinstance(colors, str):
        try:
            colors = json.loads(colors)
        except (json.JSONDecodeError, TypeError):
            colors = []
    if not colors or len(colors) < 2:
        colors = ["#1a1a2e", "#667eea"]
    return colors


def _safe_str(val, fallback=""):
    """Return string representation or fallback."""
    if val is None:
        return fallback
    return str(val)


def _esc(s):
    """HTML-escape a string."""
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


_INLINE_CSS = ""
_INLINE_JS = ""

def _head(title, extra=""):
    """Return standard HTML5 <head> with inline CSS from global cache."""
    style_tag = f"<style>{_INLINE_CSS}</style>" if _INLINE_CSS else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)}</title>
{style_tag}
{extra}
</head>
<body>"""


def _foot():
    """Standard closing tags + inline JS from global cache."""
    script_tag = f"<script>{_INLINE_JS}</script>" if _INLINE_JS else ""
    return f'{script_tag}\n</body>\n</html>'


# ---------------------------------------------------------------------------
# 1. build_css
# ---------------------------------------------------------------------------
def build_css(design, brand_kit):
    """Generate a complete style.css string from design decisions and brand colors."""
    style_recipe = design.get("style_recipe", "") or brand_kit.get("design_system", {}).get("style_recipe", "")
    recipe = STYLE_RECIPES.get(style_recipe, None) if style_recipe else None
    if recipe:
        p = recipe["palette"]
        primary = p["primary"]; accent = p["accent"]; bg = p["bg"]; text_color = p["text"]
        muted = p["muted"]; border_color = p["border"]
        radius = recipe["radius"]; shadow = recipe["shadow"]
        body_font = recipe["typography"]["body"]; heading_font = recipe["typography"]["heading"]
    else:
        colors = _brand_colors(brand_kit)
        primary = colors[0]
        accent = colors[1] if len(colors) > 1 else "#667eea"
        bg = "#ffffff"; text_color = "#1f2937"; muted = "#6b7280"; border_color = "#e5e7eb"
        radius = design.get("css_vars", {}).get("--radius", "8px")
        shadow = design.get("css_vars", {}).get("--shadow", "0 4px 24px rgba(0,0,0,0.08)")
        body_font = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
        heading_font = body_font
    muted = "#6b7280"
    border_color = "#e5e7eb"
    radius = design.get("css_vars", {}).get("--radius", "8px")
    shadow = design.get("css_vars", {}).get("--shadow", "0 4px 24px rgba(0,0,0,0.08)")
    transition = design.get("css_vars", {}).get("--transition", "0.3s ease")

    nav_style = "sticky"
    layout = design.get("layout", {})
    nav_cfg = layout.get("nav", {})
    if nav_cfg.get("style") != "sticky_top":
        nav_style = "relative"

    product_card = design.get("product_card", {})
    cols = product_card.get("columns_desktop", 4)
    card_style = product_card.get("style", "shadow")
    hover = product_card.get("hover_effect", "lift")
    show_rating = product_card.get("show_rating", True)
    show_badge = product_card.get("show_badge", True)

    components = design.get("components", [])

    cart_design = design.get("cart", {})
    cart_style = cart_design.get("style", "drawer")

    footer_cols = layout.get("footer", {}).get("columns", 4)

    animation = ""
    if design.get("animation_level") != "none":
        animation = """@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.product-card{animation:fadeIn 0.5s ease both}
.product-card:nth-child(1){animation-delay:0s}
.product-card:nth-child(2){animation-delay:0.05s}
.product-card:nth-child(3){animation-delay:0.1s}
.product-card:nth-child(4){animation-delay:0.15s}
.product-card:nth-child(5){animation-delay:0.2s}
.product-card:nth-child(6){animation-delay:0.25s}
.product-card:nth-child(7){animation-delay:0.3s}
.product-card:nth-child(8){animation-delay:0.35s}"""

    # Button styles
    css = f"""/* Static Store — generated by static_store_engine */
:root {{
  --primary: {primary};
  --primary-dark: {_darken(primary)};
  --accent: {accent};
  --accent-dark: {_darken(accent)};
  --bg: {bg};
  --text: {text_color};
  --muted: {muted};
  --border: {border_color};
  --radius: {radius};
  --shadow: {shadow};
  --transition: {transition};
  --header-height: 64px;
}}

/* Reset */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

html {{ scroll-behavior: smooth; }}

body {{
  font-family: {body_font};
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}}

img {{ max-width: 100%; height: auto; }}

a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ color: var(--accent-dark); }}

/* Container */
.container {{
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 20px;
}}

/* -------------------- Header -------------------- */
.header {{
  background: var(--primary);
  color: #fff;
  padding: 0 24px;
  height: var(--header-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: {nav_style};
  top: 0;
  z-index: 100;
  width: 100%;
}}

.header .logo {{
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.5px;
}}

.header .logo a {{
  color: #fff;
  text-decoration: none;
}}

.nav {{
  display: flex;
  align-items: center;
  gap: 20px;
}}

.nav a {{
  color: rgba(255,255,255,0.85);
  font-size: 14px;
  font-weight: 500;
  transition: color var(--transition);
  text-decoration: none;
}}

.nav a:hover {{
  color: #fff;
}}

.header-right {{
  display: flex;
  align-items: center;
  gap: 16px;
}}

.cart-icon {{
  position: relative;
  color: #fff;
  font-size: 20px;
  cursor: pointer;
  background: none;
  border: none;
  padding: 4px;
}}

.cart-count {{
  position: absolute;
  top: -6px;
  right: -8px;
  background: var(--accent);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  min-width: 18px;
  height: 18px;
  border-radius: 9px;
  display: none;
  align-items: center;
  justify-content: center;
  line-height: 1;
}}

/* -------------------- Hero -------------------- */
.hero {{
  background: linear-gradient(135deg, {primary} 0%, {accent} 100%);
  color: #fff;
  padding: 80px 24px;
  text-align: center;
}}

.hero h1 {{
  font-size: 48px;
  font-weight: 800;
  margin-bottom: 12px;
  letter-spacing: -0.5px;
}}

.hero p {{
  font-size: 18px;
  opacity: 0.92;
  max-width: 640px;
  margin: 0 auto;
  line-height: 1.7;
}}

/* -------------------- Product Grid -------------------- */
.product-grid {{
  max-width: 1280px;
  margin: 40px auto;
  padding: 0 20px;
  display: grid;
  grid-template-columns: repeat({cols}, 1fr);
  gap: 24px;
}}

@media (max-width: 1024px) {{
  .product-grid {{ grid-template-columns: repeat(3, 1fr); }}
}}

@media (max-width: 768px) {{
  .product-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}

@media (max-width: 480px) {{
  .product-grid {{ grid-template-columns: 1fr; }}
}}

/* -------------------- Product Card -------------------- */
.product-card {{
  border-radius: var(--radius);
  overflow: hidden;
  background: #fff;
  transition: transform var(--transition), box-shadow var(--transition);
  border: {"1px solid var(--border)" if card_style == "border" else "none"};
  box-shadow: {"var(--shadow)" if card_style == "shadow" else "none"};
  display: flex;
  flex-direction: column;
}}

.product-card:hover {{
  {"transform: translateY(-4px); box-shadow: 0 12px 36px rgba(0,0,0,0.12)" if hover == "lift" else
   "transform: scale(1.03)" if hover == "zoom" else
   "box-shadow: 0 0 0 3px var(--accent), 0 8px 24px rgba(0,0,0,0.1)" if hover == "glow" else
   "box-shadow: 0 12px 36px rgba(0,0,0,0.12)"};
}}

.product-card a {{
  color: inherit;
  text-decoration: none;
  display: flex;
  flex-direction: column;
  flex: 1;
}}

.product-card-img {{
  width: 100%;
  height: 260px;
  object-fit: cover;
  background: #f3f4f6;
  display: block;
}}

.product-card-body {{
  padding: 16px;
  flex: 1;
  display: flex;
  flex-direction: column;
}}

.product-card-body h3 {{
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 6px;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}}

.product-card-body .price {{
  font-size: 20px;
  font-weight: 700;
  color: var(--accent);
  margin-bottom: 4px;
}}

.product-card-body .price .original {{
  font-size: 14px;
  color: var(--muted);
  text-decoration: line-through;
  font-weight: 400;
  margin-left: 6px;
}}

.product-card-rating {{
  font-size: 13px;
  color: #f59e0b;
  margin-bottom: 4px;
}}

.product-card-badge {{
  position: absolute;
  top: 12px;
  left: 12px;
  background: var(--accent);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}

.product-card-img-wrap {{
  position: relative;
  overflow: hidden;
}}

/* -------------------- Product Detail -------------------- */
.product-detail {{
  max-width: 1280px;
  margin: 40px auto;
  padding: 0 20px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 48px;
  align-items: start;
}}

@media (max-width: 768px) {{
  .product-detail {{ grid-template-columns: 1fr; gap: 24px; }}
}}

.product-gallery {{ }}
.product-gallery .main-image {{
  width: 100%;
  border-radius: var(--radius);
  background: #f3f4f6;
  object-fit: contain;
  max-height: 500px;
}}

.product-gallery .thumbnails {{
  display: flex;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}}

.product-gallery .thumbnails img {{
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 6px;
  border: 2px solid transparent;
  cursor: pointer;
  transition: border-color var(--transition);
  background: #f3f4f6;
}}

.product-gallery .thumbnails img:hover,
.product-gallery .thumbnails img.active {{
  border-color: var(--accent);
}}

.product-info h1 {{
  font-size: 28px;
  font-weight: 700;
  margin-bottom: 8px;
  line-height: 1.3;
}}

.product-info .price {{
  font-size: 28px;
  font-weight: 700;
  color: var(--accent);
  margin-bottom: 16px;
}}

.product-info .price .original {{
  font-size: 18px;
  color: var(--muted);
  text-decoration: line-through;
  font-weight: 400;
  margin-left: 8px;
}}

.product-info .sku {{
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 4px;
}}

.product-info .description {{
  margin: 16px 0;
  line-height: 1.8;
  color: #4b5563;
}}

/* Variant selector */
.variant-selector {{
  margin: 16px 0;
}}

.variant-selector label {{
  display: block;
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 6px;
}}

.variant-selector select {{
  width: 100%;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 14px;
  background: #fff;
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%236b7280' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 12px center;
  cursor: pointer;
}}

/* Quantity input */
.quantity-input {{
  display: flex;
  align-items: center;
  gap: 0;
  margin: 16px 0;
}}

.quantity-input button {{
  width: 40px;
  height: 40px;
  border: 1px solid var(--border);
  background: #f9fafb;
  font-size: 18px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}}

.quantity-input button:first-child {{ border-radius: var(--radius) 0 0 var(--radius); }}
.quantity-input button:last-child {{ border-radius: 0 var(--radius) var(--radius) 0; }}

.quantity-input input {{
  width: 60px;
  height: 40px;
  border: 1px solid var(--border);
  border-left: none;
  border-right: none;
  text-align: center;
  font-size: 15px;
}}

/* -------------------- Breadcrumb -------------------- */
.breadcrumb {{
  max-width: 1280px;
  margin: 16px auto 0;
  padding: 0 20px;
  font-size: 13px;
  color: var(--muted);
}}

.breadcrumb a {{
  color: var(--muted);
  text-decoration: none;
}}

.breadcrumb a:hover {{
  color: var(--primary);
}}

.breadcrumb span {{
  margin: 0 6px;
}}

/* -------------------- Tabs -------------------- */
.tabs {{
  max-width: 1280px;
  margin: 40px auto 0;
  padding: 0 20px;
}}

.tab-nav {{
  display: flex;
  border-bottom: 2px solid var(--border);
  gap: 0;
  margin-bottom: 24px;
}}

.tab-nav button {{
  padding: 12px 24px;
  border: none;
  background: none;
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
  color: var(--muted);
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  transition: color var(--transition), border-color var(--transition);
}}

.tab-nav button.active,
.tab-nav button:hover {{
  color: var(--primary);
  border-bottom-color: var(--primary);
}}

.tab-panel {{
  display: none;
  line-height: 1.8;
  color: #4b5563;
}}

.tab-panel.active {{
  display: block;
}}

/* -------------------- Bloat / Cart Drawer -------------------- */
.cart-drawer {{
  position: fixed;
  top: 0;
  right: -400px;
  width: 400px;
  max-width: 90vw;
  height: 100%;
  background: #fff;
  box-shadow: -4px 0 24px rgba(0,0,0,0.15);
  z-index: 200;
  transition: right var(--transition);
  display: flex;
  flex-direction: column;
}}

.cart-drawer.open {{
  right: 0;
}}

.cart-drawer-header {{
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
}}

.cart-drawer-header h3 {{
  font-size: 18px;
}}

.cart-drawer-close {{
  background: none;
  border: none;
  font-size: 24px;
  cursor: pointer;
  color: var(--muted);
  padding: 0;
  line-height: 1;
}}

.cart-drawer-body {{
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}}

.cart-drawer-list {{
  list-style: none;
}}

.cart-drawer-item {{
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}}

.cart-drawer-item img {{
  width: 56px;
  height: 56px;
  object-fit: cover;
  border-radius: 6px;
  background: #f3f4f6;
  flex-shrink: 0;
}}

.cart-drawer-info {{
  flex: 1;
  min-width: 0;
}}

.cart-drawer-info strong {{
  display: block;
  font-size: 14px;
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}

.cart-drawer-info span {{
  font-size: 13px;
  color: var(--muted);
}}

.cart-drawer-total {{
  padding: 12px 0;
  font-size: 16px;
  text-align: right;
  border-top: 2px solid var(--border);
  margin-top: 12px;
}}

.cart-drawer-overlay {{
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0,0,0,0.4);
  z-index: 199;
  display: none;
}}

.cart-drawer.open ~ .cart-drawer-overlay {{
  display: block;
}}

/* -------------------- Bloat / Footer -------------------- */
.footer {{
  background: var(--primary);
  color: rgba(255,255,255,0.75);
  padding: 48px 24px 32px;
  margin-top: auto;
}}

.footer-grid {{
  max-width: 1280px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: repeat({footer_cols}, 1fr);
  gap: 32px;
}}

@media (max-width: 768px) {{
  .footer-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}

@media (max-width: 480px) {{
  .footer-grid {{ grid-template-columns: 1fr; }}
}}

.footer-col h4 {{
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
}}

.footer-col a {{
  display: block;
  color: rgba(255,255,255,0.65);
  font-size: 14px;
  padding: 4px 0;
  text-decoration: none;
  transition: color var(--transition);
}}

.footer-col a:hover {{
  color: #fff;
}}

.footer-bottom {{
  max-width: 1280px;
  margin: 32px auto 0;
  padding-top: 20px;
  border-top: 1px solid rgba(255,255,255,0.15);
  text-align: center;
  font-size: 13px;
}}

/* -------------------- Buttons -------------------- */
.btn {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 12px 28px;
  border-radius: var(--radius);
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition);
  border: none;
  text-decoration: none;
  gap: 8px;
}}

.btn-primary {{
  background: var(--accent);
  color: #fff;
}}

.btn-primary:hover {{
  background: var(--accent-dark);
  color: #fff;
}}

.btn-secondary {{
  background: transparent;
  color: var(--primary);
  border: 2px solid var(--primary);
}}

.btn-secondary:hover {{
  background: var(--primary);
  color: #fff;
}}

.btn-block {{
  width: 100%;
}}

.btn-lg {{
  padding: 16px 36px;
  font-size: 17px;
}}

.btn-remove {{
  background: none;
  border: none;
  color: #ef4444;
  font-size: 20px;
  cursor: pointer;
  padding: 4px 8px;
  line-height: 1;
}}

.btn-remove:hover {{
  color: #dc2626;
}}

/* -------------------- Forms -------------------- */
.form-group {{
  margin-bottom: 16px;
}}

.form-group label {{
  display: block;
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--text);
}}

.form-group input,
.form-group select,
.form-group textarea {{
  width: 100%;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 14px;
  font-family: inherit;
  transition: border-color var(--transition);
  background: #fff;
}}

.form-group input:focus,
.form-group select:focus,
.form-group textarea:focus {{
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba({_hex_to_rgb(accent)}, 0.15);
}}

/* -------------------- Checkout Steps -------------------- */
.checkout-container {{
  max-width: 700px;
  margin: 40px auto;
  padding: 0 20px;
}}

.checkout-steps {{
  display: flex;
  justify-content: center;
  gap: 0;
  margin-bottom: 32px;
}}

.checkout-step-indicator {{
  display: flex;
  align-items: center;
  gap: 8px;
}}

.checkout-step-indicator .step-num {{
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--border);
  color: var(--muted);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 14px;
}}

.checkout-step-indicator .step-num.active {{
  background: var(--accent);
  color: #fff;
}}

.checkout-step-indicator .step-num.done {{
  background: #10b981;
  color: #fff;
}}

.checkout-step-indicator .step-line {{
  width: 60px;
  height: 2px;
  background: var(--border);
  margin: 0 8px;
}}

.checkout-step {{
  background: #fff;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 32px;
  box-shadow: var(--shadow);
}}

/* -------------------- Related Products -------------------- */
.related-products {{
  max-width: 1280px;
  margin: 48px auto;
  padding: 0 20px;
}}

.related-products h2 {{
  font-size: 24px;
  margin-bottom: 24px;
  text-align: center;
}}

/* -------------------- FAQ -------------------- */
.faq-section {{
  max-width: 800px;
  margin: 48px auto;
  padding: 0 20px;
}}

.faq-section h2 {{
  font-size: 24px;
  margin-bottom: 24px;
  text-align: center;
}}

.faq-item {{
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 12px;
  overflow: hidden;
}}

.faq-item summary {{
  padding: 16px 20px;
  font-weight: 600;
  cursor: pointer;
  background: #f9fafb;
  list-style: none;
}}

.faq-item summary::-webkit-details-marker {{ display: none; }}

.faq-item summary::before {{
  content: '+';
  margin-right: 10px;
  font-weight: 700;
  color: var(--accent);
}}

.faq-item[open] summary::before {{
  content: '-';
}}

.faq-item .faq-answer {{
  padding: 0 20px 16px;
  line-height: 1.7;
  color: #4b5563;
}}

/* -------------------- Page Content (Policy Pages) -------------------- */
.page-content {{
  max-width: 800px;
  margin: 40px auto;
  padding: 0 20px;
}}

.page-content h1 {{
  font-size: 28px;
  color: var(--primary);
  margin-bottom: 20px;
}}

.page-content h2 {{
  font-size: 20px;
  margin-top: 24px;
  margin-bottom: 12px;
}}

.page-content p {{
  margin-bottom: 16px;
  color: #4b5563;
  line-height: 1.8;
}}

/* -------------------- Cart Page -------------------- */
.cart-page {{
  max-width: 900px;
  margin: 40px auto;
  padding: 0 20px;
}}

.cart-page h1 {{
  font-size: 28px;
  margin-bottom: 24px;
}}

.cart-table {{
  width: 100%;
  border-collapse: collapse;
}}

.cart-table th {{
  text-align: left;
  padding: 12px 8px;
  border-bottom: 2px solid var(--border);
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--muted);
}}

.cart-table td {{
  padding: 12px 8px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}}

.cart-summary {{
  margin-top: 24px;
  text-align: right;
  font-size: 16px;
}}

.cart-summary p {{
  margin-bottom: 8px;
}}

/* -------------------- Toast -------------------- */
.toast {{
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%) translateY(100px);
  background: #1f2937;
  color: #fff;
  padding: 12px 28px;
  border-radius: var(--radius);
  font-size: 14px;
  font-weight: 500;
  z-index: 999;
  opacity: 0;
  transition: transform 0.4s ease, opacity 0.4s ease;
  box-shadow: 0 8px 24px rgba(0,0,0,0.2);
}}

.toast.show {{
  transform: translateX(-50%) translateY(0);
  opacity: 1;
}}

.toast-error {{
  background: #ef4444;
}}

/* -------------------- Utilities -------------------- */
.sr-only {{
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0,0,0,0);
  white-space: nowrap;
  border-width: 0;
}}

.sticky-atc {{
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: #fff;
  border-top: 1px solid var(--border);
  padding: 12px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  z-index: 90;
  box-shadow: 0 -4px 12px rgba(0,0,0,0.05);
}}

.sticky-atc .price {{
  font-size: 20px;
  font-weight: 700;
  color: var(--accent);
}}

@media (min-width: 769px) {{
  .sticky-atc {{ display: none; }}
}}

/* -------------------- Order Confirmation -------------------- */
.order-confirmation {{
  max-width: 600px;
  margin: 60px auto;
  padding: 0 20px;
  text-align: center;
}}

.order-confirmation .check-icon {{
  width: 72px;
  height: 72px;
  background: #10b981;
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 36px;
  margin: 0 auto 24px;
}}

.order-confirmation h1 {{
  font-size: 28px;
  margin-bottom: 12px;
}}

.order-confirmation p {{
  color: var(--muted);
  margin-bottom: 8px;
  font-size: 16px;
}}

.order-confirmation .order-id {{
  font-size: 18px;
  font-weight: 700;
  color: var(--primary);
  margin: 16px 0;
}}

/* -------------------- Trust Badges -------------------- */
.trust-badges {{
  display: flex;
  justify-content: center;
  gap: 24px;
  flex-wrap: wrap;
  padding: 24px 0;
  color: var(--muted);
  font-size: 13px;
}}

.trust-badges span {{
  display: flex;
  align-items: center;
  gap: 6px;
}}

/* -------------------- Empty State -------------------- */
.empty-state {{
  text-align: center;
  padding: 60px 20px;
  color: var(--muted);
}}

.empty-state svg {{
  margin-bottom: 16px;
  opacity: 0.4;
}}
{animation}
"""

    # Rating stars
    if show_rating:
        css += """
/* Rating stars */
.stars{color:#f59e0b;font-size:14px;letter-spacing:1px}
"""
    return css


# ---------------------------------------------------------------------------
# 2. render_homepage
# ---------------------------------------------------------------------------
# Insert before render_homepage — 4 layout archetypes

def _render_editorial(products, design, brand_kit, brand_name, headline, subheadline):
    footer_text = _get_footer_text(brand_kit)
    cards = ""
    for p in products:
        pid, title = p.get("id", ""), _esc(p.get("title") or "Product")
        price = p.get("price")
        image = p.get("image_url", "") or p.get("images", "")
        if image and isinstance(image, str) and "|" in image: image = image.split("|")[0]
        img = f'<img src="{_esc(image)}" alt="{title}" loading="lazy" style="width:100%;aspect-ratio:4/3;object-fit:cover" onerror="this.style.display=\'none\'">' if image else '<div style="height:300px;background:#e8e4df">No Image</div>'
        cards += f'<div style="margin-bottom:80px"><a href="/products/{pid}/">{img}<h2 style="font-size:20px;font-weight:400;margin:16px 0 4px;color:var(--text)">{title}</h2><p style="font-size:16px;color:var(--muted)">${float(price or 0):.2f}</p></a></div>'
    if not cards: cards = '<p style="text-align:center;padding:80px;color:var(--muted)">Products coming soon</p>'
    return f"""{_head(f"{brand_name}")}
<header style="padding:24px 0;border-bottom:1px solid var(--border)">
  <div style="max-width:1200px;margin:0 auto;padding:0 24px;display:flex;justify-content:space-between;align-items:center">
    <a href="/" style="font-size:18px;font-weight:400;letter-spacing:0.08em;text-transform:uppercase;color:var(--text);text-decoration:none">{_esc(brand_name)}</a>
    <nav style="display:flex;gap:24px"><a href="/" style="color:var(--text);text-decoration:none;font-size:13px">Shop</a><a href="/about/" style="color:var(--text);text-decoration:none;font-size:13px">About</a><a href="/contact/" style="color:var(--text);text-decoration:none;font-size:13px">Contact</a></nav>
  </div>
</header>
<main style="max-width:1200px;margin:0 auto;padding:80px 24px">
  <div style="max-width:640px;margin:0 auto 80px;text-align:center">
    <h1 style="font-size:40px;font-weight:400;line-height:1.3;margin-bottom:16px">{_esc(headline)}</h1>
    <p style="font-size:18px;color:var(--muted);line-height:1.7">{_esc(subheadline)}</p>
  </div>
  {cards}
</main>
<footer style="border-top:1px solid var(--border);padding:40px 24px;text-align:center"><p style="font-size:12px;color:var(--muted)">{_esc(footer_text)}</p></footer>
{_foot()}"""


def _render_dark(products, design, brand_kit, brand_name, headline, subheadline):
    footer_text = _get_footer_text(brand_kit)
    cards = ""
    for p in products:
        pid, title = p.get("id", ""), _esc(p.get("title") or "Product")
        price = p.get("price")
        image = p.get("image_url", "") or p.get("images", "")
        if image and isinstance(image, str) and "|" in image: image = image.split("|")[0]
        img = f'<img src="{_esc(image)}" alt="{title}" loading="lazy" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px" onerror="this.style.display=\'none\'">' if image else '<div style="height:180px;background:#1E1F25;border-radius:6px">No Image</div>'
        cards += f'<div style="background:#16171C;border:1px solid rgba(255,255,255,0.06);border-radius:8px;overflow:hidden"><a href="/products/{pid}/" style="text-decoration:none;color:inherit">{img}<div style="padding:14px"><h3 style="font-size:14px;font-weight:500;color:#F7F8F8;margin-bottom:6px">{title[:40]}</h3><div style="display:flex;align-items:center;justify-content:space-between"><span style="font-size:14px;font-weight:600;color:#F7F8F8">${float(price or 0):.2f}</span><span style="font-size:11px;color:#9CA3AF;font-family:monospace">USD</span></div></div></a></div>'
    if not cards: cards = '<p style="text-align:center;padding:80px;color:#6B7280">Products coming soon</p>'
    return f"""{_head(f"{brand_name}")}
<style>body{{background:#08090A}}a{{color:#5E6AD2}}a:hover{{color:#7B7FE0}}</style>
<header style="padding:12px 24px;border-bottom:1px solid rgba(255,255,255,0.06);display:flex;align-items:center;justify-content:space-between;background:#08090A">
  <a href="/" style="font-weight:600;font-size:14px;color:#F7F8F8;text-decoration:none;font-family:monospace">{_esc(brand_name)}</a>
  <nav style="display:flex;gap:20px"><a href="/" style="color:#9CA3AF;text-decoration:none;font-size:13px">Home</a><a href="/about/" style="color:#9CA3AF;text-decoration:none;font-size:13px">About</a><a href="/contact/" style="color:#9CA3AF;text-decoration:none;font-size:13px">Contact</a></nav>
</header>
<section style="padding:80px 24px 40px;text-align:center;background:#08090A">
  <h1 style="font-size:44px;font-weight:700;color:#F7F8F8;letter-spacing:-0.02em;margin-bottom:12px">{_esc(headline)}</h1>
  <p style="font-size:16px;color:#9CA3AF;max-width:560px;margin:0 auto">{_esc(subheadline)}</p>
</section>
<main style="max-width:1280px;margin:0 auto;padding:0 24px 80px"><div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px">{cards}</div></main>
<footer style="border-top:1px solid rgba(255,255,255,0.06);padding:32px 24px;text-align:center;background:#08090A"><p style="font-size:12px;color:#6B7280">{_esc(footer_text)}</p></footer>
{_foot()}"""


def _render_bold(products, design, brand_kit, brand_name, headline, subheadline):
    footer_text = _get_footer_text(brand_kit)
    cards = ""
    for p in products:
        pid, title = p.get("id", ""), _esc(p.get("title") or "Product")
        price = p.get("price")
        image = p.get("image_url", "") or p.get("images", "")
        if image and isinstance(image, str) and "|" in image: image = image.split("|")[0]
        img = f'<img src="{_esc(image)}" alt="{title}" loading="lazy" style="width:100%;height:240px;object-fit:cover" onerror="this.style.display=\'none\'">' if image else '<div style="height:240px;background:#f3f4f6">No Image</div>'
        cards += f'<div><a href="/products/{pid}/" style="text-decoration:none;color:inherit">{img}<h3 style="font-size:18px;font-weight:700;margin:12px 0 4px;color:var(--text)">{title}</h3><p style="font-size:24px;font-weight:800;color:var(--text)">${float(price or 0):.2f}</p></a></div>'
    if not cards: cards = '<p style="text-align:center;padding:80px;font-size:24px;font-weight:700">Coming Soon</p>'
    return f"""{_head(f"{brand_name}")}
<header style="padding:16px 24px;display:flex;align-items:center;justify-content:space-between">
  <a href="/" style="font-size:24px;font-weight:900;color:var(--text);text-decoration:none;letter-spacing:-0.03em">{_esc(brand_name)}</a>
  <nav style="display:flex;gap:24px"><a href="/" style="color:var(--text);text-decoration:none;font-weight:600;font-size:14px">Home</a><a href="/about/" style="color:var(--text);text-decoration:none;font-weight:600;font-size:14px">About</a><a href="/contact/" style="color:var(--text);text-decoration:none;font-weight:600;font-size:14px">Contact</a></nav>
</header>
<section style="padding:120px 24px;text-align:left;max-width:960px;margin:0 auto">
  <h1 style="font-size:72px;font-weight:900;line-height:1.05;letter-spacing:-0.03em;margin-bottom:20px">{_esc(headline)}</h1>
  <p style="font-size:20px;color:var(--muted);max-width:560px;line-height:1.6">{_esc(subheadline)}</p>
</section>
<main style="max-width:1280px;margin:0 auto;padding:0 24px 80px"><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:32px">{cards}</div></main>
<footer style="border-top:2px solid var(--border);padding:48px 24px;text-align:center"><p style="font-size:12px;color:var(--muted);font-weight:600">{_esc(footer_text)}</p></footer>
{_foot()}"""


def _render_warm(products, design, brand_kit, brand_name, headline, subheadline):
    footer_text = _get_footer_text(brand_kit)
    cards = ""
    for p in products:
        pid, title = p.get("id", ""), _esc(p.get("title") or "Product")
        price = p.get("price")
        image = p.get("image_url", "") or p.get("images", "")
        if image and isinstance(image, str) and "|" in image: image = image.split("|")[0]
        img = f'<img src="{_esc(image)}" alt="{title}" loading="lazy" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:16px 16px 0 0" onerror="this.style.display=\'none\'">' if image else '<div style="height:200px;background:#f4f1de;border-radius:16px 16px 0 0">No Image</div>'
        cards += f'<div style="background:var(--bg);border-radius:18px;overflow:hidden;box-shadow:4px 4px 0 rgba(0,0,0,0.08);border:2px solid var(--border)"><a href="/products/{pid}/" style="text-decoration:none;color:inherit">{img}<div style="padding:16px"><h3 style="font-size:16px;font-weight:600;color:var(--text);margin-bottom:8px">{title[:40]}</h3><span style="font-size:18px;font-weight:700;color:var(--accent)">${float(price or 0):.2f}</span></div></a></div>'
    if not cards: cards = '<p style="text-align:center;padding:60px;color:var(--muted);font-size:18px">Products coming soon!</p>'
    return f"""{_head(f"{brand_name}")}
<header style="padding:16px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid var(--border)">
  <a href="/" style="font-size:20px;font-weight:700;color:var(--text);text-decoration:none">{_esc(brand_name)}</a>
  <nav style="display:flex;gap:20px"><a href="/" style="color:var(--text);text-decoration:none;font-weight:500;font-size:14px">Home</a><a href="/about/" style="color:var(--text);text-decoration:none;font-weight:500;font-size:14px">About</a><a href="/contact/" style="color:var(--text);text-decoration:none;font-weight:500;font-size:14px">Contact</a></nav>
</header>
<section style="padding:80px 24px;text-align:center;border-radius:0 0 32px 32px;margin-bottom:40px">
  <h1 style="font-size:48px;font-weight:800;margin-bottom:12px">{_esc(headline)}</h1>
  <p style="font-size:18px;color:var(--muted);max-width:500px;margin:0 auto">{_esc(subheadline)}</p>
</section>
<main style="max-width:1200px;margin:0 auto;padding:0 24px 80px"><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px">{cards}</div></main>
<footer style="border-top:2px solid var(--border);padding:40px 24px;text-align:center;border-radius:32px 32px 0 0"><p style="font-size:13px;color:var(--muted)">{_esc(footer_text)}</p></footer>
{_foot()}"""


# ====================================================================
# AI-generated unique templates per recipe (cached)
# ====================================================================

import requests as _http_req

def _render_ai(products, design, brand_kit, brand_name, headline, subheadline):
    """Generate a unique HTML template for the current recipe via DeepSeek.
    Cached to backend/recipe_templates/ for subsequent use."""
    style_recipe = design.get("style_recipe", "") or brand_kit.get("design_system", {}).get("style_recipe", "")
    recipe = STYLE_RECIPES.get(style_recipe, {})
    if not style_recipe or not recipe:
        return None  # fall back to archetype dispatch
    
    # Cache directory
    
    # Try AI-generated unique template first
    ai_html = _render_ai(products, design, brand_kit, brand_name, headline, subheadline)
    if ai_html:
        return ai_html
    
    import os as _os
    cache_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "recipe_templates")
    _os.makedirs(cache_dir, exist_ok=True)
    cache_path = _os.path.join(cache_dir, f"{style_recipe}.html")
    
    # Check cache
    template = None
    if _os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            template = f.read()
    
    if not template:
        # Build DeepSeek prompt with recipe data
        p = recipe.get("palette", {})
        recipe_info = f"""Palette: primary={p.get("primary")} accent={p.get("accent")} bg={p.get("bg")} text={p.get("text")} muted={p.get("muted")} border={p.get("border")}
Typography: {recipe.get("typography", "Inter, sans-serif")}
Radius: {recipe.get("radius", "8px")} Shadow: {recipe.get("shadow", "none")}
Signature: {recipe.get("signature", "")}
Avoid: {recipe.get("anti_patterns", "")}"""
        prompt = f"""Generate a COMPLETE e-commerce homepage HTML for: {recipe.get("name", style_recipe)}

RECIPE: {recipe_info}

REQUIREMENTS:
- Brand: {brand_name}, Headline: {headline}
- Must be valid HTML5 with inline CSS (no external deps)
- Include: header nav, hero section, product grid placeholder, footer
- Use ONLY the recipe palette colors. No AI-cliche gradients.
- Fonts: use the recipe typography if specified, else system fonts
- Radius and shadow must match recipe values
- Mobile responsive with viewport meta
- Products placeholder: {{PRODUCTS}}
- Footer text placeholder: {{FOOTER}}
- Headline placeholder: {{HEADLINE}}
- Subheadline placeholder: {{SUBHEADLINE}}
- Brand name placeholder: {{BRAND}}

Output ONLY the HTML. No markdown, no explanation."""
        try:
            from services.api_key_rotator import get_deepseek_keys, rotate_deepseek
            keys = get_deepseek_keys()
            if keys:
                def _call(key):
                    resp = _http_req.post(
                        "https://api.deepseek.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": "deepseek-chat", "messages": [
                            {"role": "system", "content": "You are a senior web designer. Generate only valid HTML. No markdown."},
                            {"role": "user", "content": prompt}
                        ], "temperature": 0.7, "max_tokens": 4000}, timeout=60)
                    if resp.status_code == 200:
                        return resp.json()["choices"][0]["message"]["content"].strip()
                    return None
                template = rotate_deepseek(_call, keys)
                if template:
                    template = template.lstrip("```html").lstrip("```").rstrip("```").strip()
                    if template.startswith("<!DOCTYPE") or template.startswith("<html"):
                        with open(cache_path, "w", encoding="utf-8") as f:
                            f.write(template)
                        logger.info(f"AI template cached: {style_recipe}")
        except Exception as e:
            logger.warning(f"AI template generation failed for {style_recipe}: {e}")
    
    if not template:
        return None
    
    # Build product cards
    cards = ""
    for p in products:
        pid = p.get("id", ""); title = _esc(p.get("title") or "Product")
        price = p.get("price"); image = p.get("image_url", "") or p.get("images", "")
        if image and isinstance(image, str) and "|" in image: image = image.split("|")[0]
        img = f"<img src=\"{_esc(image)}\" alt=\"{title}\">" if image else f"<div>No Image</div>"
        cards += f"<a href=\"/products/{pid}/\" class=\"product-card\">{img}<h3>{title}</h3><span>${float(price or 0):.2f}</span></a>"

    if not cards: cards = "<p>Products coming soon</p>"
    footer_text = _get_footer_text(brand_kit)
    
    # Fill placeholders
    html = template.replace("{{BRAND}}", _esc(brand_name))
    html = html.replace("{{HEADLINE}}", _esc(headline))
    html = html.replace("{{SUBHEADLINE}}", _esc(subheadline))
    html = html.replace("{{PRODUCTS}}", cards)
    html = html.replace("{{FOOTER}}", _esc(footer_text))
    return html

def render_homepage(products, design, brand_kit):
    """Dispatch to layout archetype based on style_recipe."""
    brand_name = _safe_str(brand_kit.get("brand_name") or brand_kit.get("name", ""), "Store")
    style_recipe = design.get("style_recipe", "") or brand_kit.get("design_system", {}).get("style_recipe", "")
    hero = design.get("layout", {}).get("hero", {})
    headline = hero.get("headline", f"Welcome to {brand_name}")
    subheadline = hero.get("subheadline", "Discover our collection")
    
    editorial = {"aesop","muji-kenya-hara","tufte-dataink","monocle-magazine","nyt-the-daily"}
    dark = {"linear","vercel-mesh","bloomberg-terminal","y2k-retrofuturism","raycast","notion-pre-ai","field-io","active-theory","resn-storytelling","are-na","balenciaga-post-2017"}
    bold = {"pentagram","vignelli-swiss-helvetica","stripe-press","apple-hig","dieter-rams-braun","bloomberg-businessweek-turley"}
    warm = {"mid-century-modern","mailchimp-freddie","headspace-meditation"}
    
    # Try AI-generated unique template first
    ai_html = _render_ai(products, design, brand_kit, brand_name, headline, subheadline)
    if ai_html:
        return ai_html

    if style_recipe in editorial:
        return _render_editorial(products, design, brand_kit, brand_name, headline, subheadline)
    elif style_recipe in dark:
        return _render_dark(products, design, brand_kit, brand_name, headline, subheadline)
    elif style_recipe in bold:
        return _render_bold(products, design, brand_kit, brand_name, headline, subheadline)
    elif style_recipe in warm:
        return _render_warm(products, design, brand_kit, brand_name, headline, subheadline)
    
    # Fallback: original template
    brand_name = _safe_str(brand_kit.get("brand_name") or brand_kit.get("name", ""), "Store")
    colors = _brand_colors(brand_kit)
    primary = colors[0]
    style_recipe = design.get("style_recipe", "") or brand_kit.get("design_system", {}).get("style_recipe", "")
    recipe = STYLE_RECIPES.get(style_recipe, None) if style_recipe else None
    is_dark = recipe and ("dark" in (recipe.get("school","") or "").lower() or style_recipe in ("linear","vercel-mesh","bloomberg-terminal","y2k-retrofuturism"))
    is_editorial = recipe and ("editorial" in (recipe.get("school","") or "").lower() or style_recipe in ("aesop","muji-kenya-hara","monocle-magazine","tufte-dataink","nyt-the-daily"))
    accent = colors[1] if len(colors) > 1 else "#667eea"


    # Recipe layout archetypes
    archetype = 'default'
    if style_recipe in ('aesop','muji-kenya-hara','tufte-dataink','monocle-magazine','nyt-the-daily'):
        archetype = 'editorial'
    elif style_recipe in ('linear','vercel-mesh','bloomberg-terminal','y2k-retrofuturism','raycast','notion-pre-ai','field-io','active-theory','resn-storytelling','are-na','balenciaga-post-2017'):
        archetype = 'dark'
    elif style_recipe in ('pentagram','vignelli-swiss-helvetica','stripe-press','apple-hig','dieter-rams-braun','bloomberg-businessweek-turley'):
        archetype = 'bold'
    elif style_recipe in ('mid-century-modern','mailchimp-freddie','headspace-meditation'):
        archetype = 'warm'
    is_editorial = archetype == 'editorial'
    is_dark = archetype == 'dark'
    is_bold = archetype == 'bold'
    is_warm = archetype == 'warm'
    hero = design.get("layout", {}).get("hero", {})
    headline = hero.get("headline", f"Welcome to {brand_name}")
    subheadline = hero.get("subheadline", "Discover our collection")

    product_card_cfg = design.get("product_card", {})
    show_rating = product_card_cfg.get("show_rating", True)
    show_badge = product_card_cfg.get("show_badge", True)
    components = set(design.get("components", []))

    footer_text = _get_footer_text(brand_kit)

    # Build product grid
    cards = ""
    for p in products:
        pid = p.get("id", "")
        title = _esc(p.get("title") or "Product")
        price = p.get("price")
        sale_price = p.get("sale_price")
        image = p.get("image_url", "") or p.get("images", "")
        # Handle pipe-separated image URLs (woocommerce_products format)
        if image and isinstance(image, str) and "|" in image:
            image = image.split("|")[0]
        currency = p.get("currency", "USD")
        is_on_sale = sale_price is not None and float(sale_price) > 0 and float(sale_price) < float(price or 0)

        # Price display
        display_price = sale_price if is_on_sale else price
        price_html = ""
        try:
            price_html = f'${float(display_price or 0):.2f}'
        except (ValueError, TypeError):
            price_html = "$0.00"
        if is_on_sale:
            try:
                price_html += f' <span class="original">${float(price or 0):.2f}</span>'
            except (ValueError, TypeError):
                pass

        # Image
        img_tag = ""
        if image:
            img_tag = f'<img src="{_esc(image)}" alt="{title}" class="product-card-img" loading="lazy" onerror="this.style.display=\'none\'">'
        else:
            img_tag = f'<div style="height:260px;background:#e5e7eb;display:flex;align-items:center;justify-content:center;color:#9ca3af;font-size:14px">No Image</div>'

        badge_html = ""
        if show_badge and is_on_sale:
            badge_html = '<div class="product-card-badge">Sale</div>'

        rating_html = ""
        if show_rating:
            rating_html = '<div class="product-card-rating"><span class="stars">&#9733;&#9733;&#9733;&#9733;&#9733;</span></div>'

        cards += f"""<div class="product-card">
  <a href="/products/{pid}/">
    <div class="product-card-img-wrap">{badge_html}{img_tag}</div>
    <div class="product-card-body">
      <h3>{title}</h3>
      {rating_html}
      <div class="price">{price_html}</div>
    </div>
  </a>
</div>"""

    if not cards:
        cards = '<div class="empty-state"><svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg><p style="font-size:18px">Products coming soon!</p><p>Check back later for our new collection.</p></div>'

    hero_extra = ""
    if is_editorial: hero_extra = "min-height:50vh;display:flex;flex-direction:column;justify-content:center;text-align:left;max-width:720px;margin:0 auto;padding:80px 24px"
    elif is_dark: hero_extra = "background:#08090A;color:#F7F8F8;border-bottom:1px solid rgba(255,255,255,0.06);padding:60px 24px"
    elif is_bold: hero_extra = "padding:100px 24px 60px;text-align:left"
    elif is_warm: hero_extra = "background:var(--bg);border-radius:0 0 48px 48px;padding:80px 24px"

    trust_html = ""
    if "trust_badges" in components:
        trust_html = """<div class="trust-badges"><span>&#128274; Secure Checkout</span><span>&#128666; Free Shipping</span><span>&#128260; 30-Day Returns</span><span>&#128222; 24/7 Support</span></div>"""

    html = f"""{_head(f"{brand_name} - Shop")}
<header class="header">
  <div class="logo"><a href="/">{_esc(brand_name)}</a></div>
  <nav class="nav">
    <a href="/">Home</a>
    <a href="/about/">About</a>
    <a href="/contact/">Contact</a>
  </nav>
  <div class="header-right">
    <button class="cart-icon" id="cart-toggle" aria-label="Cart">
      &#128722;
      <span class="cart-count" id="cart-count">0</span>
    </button>
  </div>
</header>

<section class="hero" style="{hero_extra}">
  <h1>{_esc(headline)}</h1>
  <p>{_esc(subheadline)}</p>
</section>

{trust_html}

<main class="container" style="margin-top:40px;margin-bottom:40px">
  <div class="product-grid" id="products" style="gap:{48 if is_editorial else 32 if is_warm else 24}px">
    {cards}
  </div>
</main>

<div class="cart-drawer" id="cart-drawer">
  <div class="cart-drawer-header">
    <h3>Cart</h3>
    <button class="cart-drawer-close" id="cart-close">&times;</button>
  </div>
  <div class="cart-drawer-body" id="cart-drawer-body">
    <p style="text-align:center;color:#999;padding:40px">Your cart is empty</p>
  </div>
</div>
<div class="cart-drawer-overlay"></div>

<footer class="footer">
  <div class="footer-grid">
    <div class="footer-col">
      <h4>Shop</h4>
      <a href="/">All Products</a>
      <a href="/about/">About Us</a>
      <a href="/contact/">Contact</a>
    </div>
    <div class="footer-col">
      <h4>Support</h4>
      <a href="/shipping/">Shipping</a>
      <a href="/returns/">Returns</a>
      <a href="/faq/">FAQ</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy/">Privacy Policy</a>
      <a href="/terms/">Terms of Service</a>
    </div>
    <div class="footer-col">
      <h4>Contact</h4>
      <a href="/contact/">Get in Touch</a>
      <p style="color:rgba(255,255,255,0.5);font-size:13px;margin-top:8px">We're here to help</p>
    </div>
  </div>
  <div class="footer-bottom">
    <p>{_esc(footer_text)}</p>
  </div>
</footer>

{_foot()}"""
    return html


# ---------------------------------------------------------------------------
# 3. render_product_page
# ---------------------------------------------------------------------------
def render_product_page(product, design, brand_kit, all_products):
    """Generate a complete product detail page with JSON-LD schema."""
    brand_name = _safe_str(brand_kit.get("brand_name") or brand_kit.get("name", ""), "Store")
    colors = _brand_colors(brand_kit)
    accent = colors[1] if len(colors) > 1 else "#667eea"

    detail_cfg = design.get("product_detail", {})
    show_breadcrumb = detail_cfg.get("show_breadcrumb", True)
    show_sku = detail_cfg.get("show_sku", True)
    show_tabs = detail_cfg.get("tabs", True)
    show_share = detail_cfg.get("show_share", True)
    sticky_atc = detail_cfg.get("sticky_atc", True)
    components = set(design.get("components", []))

    pid = product.get("id", "")
    title = _esc(product.get("title") or "Product")
    description = _safe_str(product.get("description"), "No description available.")
    price = product.get("price")
    sale_price = product.get("sale_price")
    currency = product.get("currency", "USD")
    sku = _safe_str(product.get("sku"), "")
    brand = _safe_str(product.get("brand") or brand_name)
    mpn = _safe_str(product.get("mpn"), "")
    gtin = _safe_str(product.get("gtin"), "")
    availability = product.get("availability", "in_stock")
    condition = product.get("condition", "new")
    image_url = product.get("image_url", "") or product.get("images", "")
    if image_url and isinstance(image_url, str) and "|" in image_url:
        image_url = image_url.split("|")[0]

    additional_images = product.get("additional_images", [])
    if isinstance(additional_images, str):
        try:
            additional_images = json.loads(additional_images)
        except (json.JSONDecodeError, TypeError):
            additional_images = []

    variant_data = product.get("variant_data", {})
    if isinstance(variant_data, str):
        try:
            variant_data = json.loads(variant_data)
        except (json.JSONDecodeError, TypeError):
            variant_data = {}

    is_on_sale = sale_price is not None and float(sale_price) > 0 and float(sale_price) < float(price or 0)
    display_price = sale_price if is_on_sale else price
    try:
        display_price_str = f"${float(display_price or 0):.2f}"
    except (ValueError, TypeError):
        display_price_str = "$0.00"

    original_price_str = ""
    if is_on_sale:
        try:
            original_price_str = f' <span class="original">${float(price or 0):.2f}</span>'
        except (ValueError, TypeError):
            pass

    # Availability for JSON-LD
    avail_map = {"in_stock": "https://schema.org/InStock", "out_of_stock": "https://schema.org/OutOfStock", "preorder": "https://schema.org/PreOrder"}
    schema_avail = avail_map.get(availability, "https://schema.org/InStock")
    condition_map = {"new": "https://schema.org/NewCondition", "refurbished": "https://schema.org/RefurbishedCondition", "used": "https://schema.org/UsedCondition"}
    schema_condition = condition_map.get(condition, "https://schema.org/NewCondition")

    # ---- JSON-LD ----
    json_ld_offers = {
        "@type": "Offer",
        "price": str(display_price) if display_price is not None else "0",
        "priceCurrency": currency,
        "availability": schema_avail,
    }
    if is_on_sale:
        # Add list price if on sale
        json_ld_offers["priceSpecification"] = {
            "@type": "UnitPriceSpecification",
            "price": str(display_price),
            "priceCurrency": currency,
        }

    json_ld = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product.get("title", "Product"),
        "description": description[:5000],
        "sku": sku,
        "brand": {"@type": "Brand", "name": brand},
        "offers": json_ld_offers,
    }
    if image_url:
        json_ld["image"] = image_url
    if additional_images:
        json_ld["image"] = [image_url] + additional_images if image_url else additional_images
    if mpn:
        json_ld["mpn"] = mpn
    if gtin:
        json_ld["gtin"] = gtin

    json_ld_script = f'\n<script type="application/ld+json">\n{json.dumps(json_ld, ensure_ascii=False)}\n</script>'

    # ---- Image Gallery ----
    gallery_cfg = detail_cfg.get("gallery", "thumbnails_left")
    all_images = [image_url] + additional_images if image_url else additional_images

    gallery_html = ""
    if all_images:
        main_img = all_images[0]
        gallery_html = f'<div class="product-gallery">\n  <img src="{_esc(main_img)}" alt="{title}" class="main-image" id="main-image" onerror="this.src=\'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22600%22 height=%22500%22%3E%3Crect fill=%22%23f3f4f6%22 width=%22600%22 height=%22500%22/%3E%3Ctext x=%2250%25%22 y=%2250%25%22 text-anchor=%22middle%22 dy=%22.3em%22 fill=%22%239ca3af%22%3ENo Image%3C/text%3E%3C/svg%3E\'">\n'
        if len(all_images) > 1:
            gallery_html += '  <div class="thumbnails">\n'
            for idx, img in enumerate(all_images):
                active_class = " active" if idx == 0 else ""
                gallery_html += f'    <img src="{_esc(img)}" alt="{title} - view {idx+1}" class="thumb{active_class}" onclick="document.getElementById(\'main-image\').src=this.src;this.parentNode.querySelectorAll(\'img\').forEach(function(i){{i.classList.remove(\'active\')}});this.classList.add(\'active\')" onerror="this.style.display=\'none\'">\n'
            gallery_html += '  </div>\n'
        gallery_html += '</div>'
    else:
        gallery_html = '<div class="product-gallery"><div style="width:100%;height:400px;background:#f3f4f6;display:flex;align-items:center;justify-content:center;color:#9ca3af;border-radius:var(--radius)">No Image Available</div></div>'

    # ---- Breadcrumb ----
    breadcrumb_html = ""
    if show_breadcrumb:
        breadcrumb_html = f"""<nav class="breadcrumb" aria-label="Breadcrumb">
  <a href="/">Home</a> <span>/</span> <a href="/">Products</a> <span>/</span> <span>{title}</span>
</nav>"""

    # ---- SKU ----
    sku_html = ""
    if show_sku and sku:
        sku_html = f'<p class="sku">SKU: {_esc(sku)}</p>'

    # ---- Variant Selector ----
    variant_html = ""
    if variant_data and isinstance(variant_data, dict):
        for var_name, options in variant_data.items():
            if isinstance(options, list) and options:
                opts_html = "".join(f'<option value="{_esc(str(o))}">{_esc(str(o))}</option>' for o in options)
                variant_html += f"""<div class="variant-selector">
  <label for="variant-{_esc(var_name)}">{_esc(var_name)}</label>
  <select id="variant-{_esc(var_name)}" name="{_esc(var_name)}">
    {opts_html}
  </select>
</div>"""

    # ---- Tabs ----
    tabs_html = ""
    if show_tabs:
        details = product.get("description", "")
        # Use a secondary description or shipping info if available
        additional_info = ""
        shipping_weight = _safe_str(product.get("shipping_weight"), "")
        shipping_unit = product.get("shipping_weight_unit", "kg")
        if shipping_weight:
            additional_info += f"<p><strong>Weight:</strong> {_esc(shipping_weight)} {_esc(shipping_unit)}</p>"
        additional_info += f"<p><strong>Brand:</strong> {_esc(brand)}</p>"
        additional_info += f"<p><strong>Condition:</strong> {condition.capitalize()}</p>"
        additional_info += f"<p><strong>Availability:</strong> {availability.replace('_', ' ').title()}</p>"

        tabs_html = f"""<div class="tabs">
  <div class="tab-nav">
    <button class="active" onclick="document.querySelectorAll('.tab-panel').forEach(function(p){{p.classList.remove('active')}});document.querySelectorAll('.tab-nav button').forEach(function(b){{b.classList.remove('active')}});document.getElementById('tab-desc').classList.add('active');this.classList.add('active')">Description</button>
    <button onclick="document.querySelectorAll('.tab-panel').forEach(function(p){{p.classList.remove('active')}});document.querySelectorAll('.tab-nav button').forEach(function(b){{b.classList.remove('active')}});document.getElementById('tab-details').classList.add('active');this.classList.add('active')">Details</button>
  </div>
  <div class="tab-panel active" id="tab-desc">
    <p>{_esc(description)}</p>
  </div>
  <div class="tab-panel" id="tab-details">
    {additional_info}
  </div>
</div>"""

    # ---- Sticky ATC ----
    sticky_html = ""
    if sticky_atc:
        sticky_html = f"""<div class="sticky-atc">
  <span class="price">{display_price_str}{original_price_str}</span>
  <button class="btn btn-primary add-to-cart-btn" data-id="{pid}" data-title="{title}" data-price="{display_price or 0}" data-image="{_esc(image_url)}">Add to Cart</button>
</div>"""

    # ---- Related Products ----
    related_html = ""
    if "related_products" in components and all_products:
        related = [p for p in all_products if str(p.get("id")) != str(pid)][:4]
        if related:
            cards = ""
            for rp in related:
                rid = rp.get("id", "")
                rtitle = _esc(rp.get("title") or "Product")
                rprice = rp.get("price")
                rsale = rp.get("sale_price")
                rimg = rp.get("image_url", "")
                r_is_sale = rsale is not None and float(rsale) > 0 and float(rsale) < float(rprice or 0)
                r_display = rsale if r_is_sale else rprice
                try:
                    r_price_str = f'${float(r_display or 0):.2f}'
                except (ValueError, TypeError):
                    r_price_str = "$0.00"
                r_img_tag = f'<img src="{_esc(rimg)}" alt="{rtitle}" class="product-card-img" loading="lazy" onerror="this.style.display=\'none\'">' if rimg else '<div style="height:200px;background:#f3f4f6;display:flex;align-items:center;justify-content:center;color:#9ca3af">No Image</div>'
                cards += f"""<div class="product-card">
  <a href="/products/{rid}/">
    <div class="product-card-img-wrap">{r_img_tag}</div>
    <div class="product-card-body">
      <h3>{rtitle}</h3>
      <div class="price">{r_price_str}</div>
    </div>
  </a>
</div>"""
            related_html = f"""<section class="related-products">
  <h2>You May Also Like</h2>
  <div class="product-grid">
    {cards}
  </div>
</section>"""

    # ---- FAQ ----
    faq_html = ""
    if "faq" in components:
        faq_html = """<section class="faq-section">
  <h2>Frequently Asked Questions</h2>
  <details class="faq-item"><summary>What is your shipping policy?</summary><div class="faq-answer"><p>We offer worldwide shipping with delivery times varying by location. Free standard shipping on orders over $50.</p></div></details>
  <details class="faq-item"><summary>What is your return policy?</summary><div class="faq-answer"><p>You can return most items within 30 days of delivery. Items must be unused and in original packaging.</p></div></details>
  <details class="faq-item"><summary>How can I contact support?</summary><div class="faq-answer"><p>Our support team is available 24/7. Visit our <a href="/contact/">Contact page</a> for details.</p></div></details>
  <details class="faq-item"><summary>Are my payment details secure?</summary><div class="faq-answer"><p>Yes. All transactions are processed securely using industry-standard encryption.</p></div></details>
</section>"""

    # ---- Footer ----
    footer_text = _get_footer_text(brand_kit)

    html = f"""{_head(f"{title} - {brand_name}", extra=json_ld_script)}
<header class="header">
  <div class="logo"><a href="/">{_esc(brand_name)}</a></div>
  <nav class="nav">
    <a href="/">Home</a>
    <a href="/about/">About</a>
    <a href="/contact/">Contact</a>
  </nav>
  <div class="header-right">
    <button class="cart-icon" id="cart-toggle" aria-label="Cart">
      &#128722;
      <span class="cart-count" id="cart-count">0</span>
    </button>
  </div>
</header>

{breadcrumb_html}

<main class="product-detail">
  {gallery_html}

  <div class="product-info">
    <h1>{title}</h1>
    <div class="price">{display_price_str}{original_price_str}</div>
    {sku_html}

    {variant_html}

    <div class="description">
      <p>{_esc(description)}</p>
    </div>

    <div class="quantity-input">
      <button type="button" id="qty-minus">-</button>
      <input type="number" id="qty-input" value="1" min="1" style="width:60px;text-align:center" aria-label="Quantity">
      <button type="button" id="qty-plus">+</button>
    </div>

    <button class="btn btn-primary btn-lg btn-block add-to-cart-btn" data-id="{pid}" data-title="{title}" data-price="{display_price or 0}" data-image="{_esc(image_url)}">
      Add to Cart - {display_price_str}
    </button>

    <div style="display:flex;align-items:center;gap:24px;margin-top:16px;padding-top:16px;border-top:1px solid var(--border);flex-wrap:wrap">
      <span style="font-size:13px;color:var(--muted)">&#128666; Free Shipping</span>
      <span style="font-size:13px;color:var(--muted)">&#128260; Easy Returns</span>
      <span style="font-size:13px;color:var(--muted)">&#128274; Secure Payment</span>
    </div>
  </div>
</main>

{tabs_html}
{related_html}
{faq_html}

<div class="cart-drawer" id="cart-drawer">
  <div class="cart-drawer-header">
    <h3>Cart</h3>
    <button class="cart-drawer-close" id="cart-close">&times;</button>
  </div>
  <div class="cart-drawer-body" id="cart-drawer-body">
    <p style="text-align:center;color:#999;padding:40px">Your cart is empty</p>
  </div>
</div>
<div class="cart-drawer-overlay"></div>

{sticky_html}

<footer class="footer">
  <div class="footer-grid">
    <div class="footer-col">
      <h4>Shop</h4>
      <a href="/">All Products</a>
      <a href="/about/">About Us</a>
      <a href="/contact/">Contact</a>
    </div>
    <div class="footer-col">
      <h4>Support</h4>
      <a href="/shipping/">Shipping</a>
      <a href="/returns/">Returns</a>
      <a href="/faq/">FAQ</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy/">Privacy Policy</a>
      <a href="/terms/">Terms of Service</a>
    </div>
    <div class="footer-col">
      <h4>Contact</h4>
      <a href="/contact/">Get in Touch</a>
      <p style="color:rgba(255,255,255,0.5);font-size:13px;margin-top:8px">We're here to help</p>
    </div>
  </div>
  <div class="footer-bottom">
    <p>{_esc(footer_text)}</p>
  </div>
</footer>

{_foot()}"""
    return html


# ---------------------------------------------------------------------------
# 4. render_cart_page
# ---------------------------------------------------------------------------
def render_cart_page(design, brand_kit):
    """Generate the shopping cart page (reads from localStorage)."""
    brand_name = _safe_str(brand_kit.get("brand_name") or brand_kit.get("name", ""), "Store")
    footer_text = _get_footer_text(brand_kit)

    html = f"""{_head(f"Cart - {brand_name}")}
<header class="header">
  <div class="logo"><a href="/">{_esc(brand_name)}</a></div>
  <nav class="nav">
    <a href="/">Home</a>
    <a href="/about/">About</a>
    <a href="/contact/">Contact</a>
  </nav>
  <div class="header-right">
    <button class="cart-icon" id="cart-toggle" aria-label="Cart">
      &#128722;
      <span class="cart-count" id="cart-count">0</span>
    </button>
  </div>
</header>

<main class="cart-page">
  <h1>Shopping Cart</h1>
  <table class="cart-table">
    <thead>
      <tr>
        <th></th>
        <th>Product</th>
        <th>Price</th>
        <th>Quantity</th>
        <th>Total</th>
        <th></th>
      </tr>
    </thead>
    <tbody id="cart-table-body">
      <tr><td colspan="6" style="text-align:center;padding:40px;color:#999">Loading cart...</td></tr>
    </tbody>
  </table>
  <div class="cart-summary" id="cart-summary"></div>
  <div style="text-align:center;margin-top:24px">
    <a href="/" class="btn btn-secondary">Continue Shopping</a>
  </div>
</main>

<div class="cart-drawer" id="cart-drawer">
  <div class="cart-drawer-header">
    <h3>Cart</h3>
    <button class="cart-drawer-close" id="cart-close">&times;</button>
  </div>
  <div class="cart-drawer-body" id="cart-drawer-body">
    <p style="text-align:center;color:#999;padding:40px">Your cart is empty</p>
  </div>
</div>
<div class="cart-drawer-overlay"></div>

<footer class="footer">
  <div class="footer-grid">
    <div class="footer-col">
      <h4>Shop</h4>
      <a href="/">All Products</a>
      <a href="/about/">About Us</a>
      <a href="/contact/">Contact</a>
    </div>
    <div class="footer-col">
      <h4>Support</h4>
      <a href="/shipping/">Shipping</a>
      <a href="/returns/">Returns</a>
      <a href="/faq/">FAQ</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy/">Privacy Policy</a>
      <a href="/terms/">Terms of Service</a>
    </div>
    <div class="footer-col">
      <h4>Contact</h4>
      <a href="/contact/">Get in Touch</a>
      <p style="color:rgba(255,255,255,0.5);font-size:13px;margin-top:8px">We're here to help</p>
    </div>
  </div>
  <div class="footer-bottom">
    <p>{_esc(footer_text)}</p>
  </div>
</footer>

{_foot()}"""
    return html


# ---------------------------------------------------------------------------
# 5. render_checkout_page
# ---------------------------------------------------------------------------
def render_checkout_page(design, brand_kit):
    """Generate 3-step checkout page: Shipping -> Payment (simulated) -> Review & Place Order."""
    brand_name = _safe_str(brand_kit.get("brand_name") or brand_kit.get("name", ""), "Store")
    footer_text = _get_footer_text(brand_kit)
    checkout_cfg = design.get("checkout", {})
    steps = checkout_cfg.get("steps", ["shipping", "payment", "review"])
    show_coupon = checkout_cfg.get("show_coupon", False)
    show_order_summary = checkout_cfg.get("show_order_summary", True)

    # Step labels
    step_labels = []
    for s in steps:
        if s == "shipping":
            step_labels.append("Shipping")
        elif s == "payment":
            step_labels.append("Payment")
        elif s == "review":
            step_labels.append("Review")
        else:
            step_labels.append(s.capitalize())

    # Step indicators
    indicator_html = ""
    for i, label in enumerate(step_labels):
        if i > 0:
            indicator_html += '<span class="step-line"></span>'
        indicator_html += f'<span class="step-num" id="step-num-{i+1}">{i+1}</span>'
        indicator_html += f'<span style="font-size:13px;color:var(--muted)">{label}</span>'

    coupon_html = ""
    if show_coupon:
        coupon_html = """<div class="form-group">
  <label for="coupon">Coupon Code</label>
  <div style="display:flex;gap:8px">
    <input type="text" id="coupon" placeholder="Enter code" style="flex:1">
    <button class="btn btn-secondary" type="button">Apply</button>
  </div>
</div>"""

    order_summary_html = ""
    if show_order_summary:
        order_summary_html = """<div style="margin-top:20px;padding:16px;background:#f9fafb;border-radius:var(--radius);border:1px solid var(--border)">
  <p><strong>Order Summary</strong></p>
  <div id="checkout-summary" style="margin-top:8px"><p style="color:var(--muted);font-size:13px">Calculating...</p></div>
</div>"""

    html = f"""{_head(f"Checkout - {brand_name}")}
<header class="header">
  <div class="logo"><a href="/">{_esc(brand_name)}</a></div>
  <nav class="nav">
    <a href="/">Home</a>
    <a href="/cart/">Cart</a>
  </nav>
</header>

<main class="checkout-container">
  <h1 style="text-align:center;margin-bottom:24px">Checkout</h1>

  <div class="checkout-steps">
    {indicator_html}
  </div>

  <!-- Step 1: Shipping -->
  <div class="checkout-step" id="checkout-step-1">
    <h2 style="margin-bottom:20px;font-size:20px">Shipping Information</h2>
    <div class="form-group">
      <label for="fullname">Full Name</label>
      <input type="text" id="fullname" placeholder="John Doe" required>
    </div>
    <div class="form-group">
      <label for="email">Email Address</label>
      <input type="email" id="email" placeholder="john@example.com" required>
    </div>
    <div class="form-group">
      <label for="address">Address</label>
      <input type="text" id="address" placeholder="123 Main St" required>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="form-group">
        <label for="city">City</label>
        <input type="text" id="city" placeholder="City" required>
      </div>
      <div class="form-group">
        <label for="zip">ZIP / Postal Code</label>
        <input type="text" id="zip" placeholder="12345" required>
      </div>
    </div>
    <div class="form-group">
      <label for="country">Country</label>
      <select id="country">
        <option value="US">United States</option>
        <option value="GB">United Kingdom</option>
        <option value="CA">Canada</option>
        <option value="AU">Australia</option>
        <option value="DE">Germany</option>
        <option value="FR">France</option>
        <option value="JP">Japan</option>
        <option value="CN">China</option>
        <option value="Other">Other</option>
      </select>
    </div>
    <button class="btn btn-primary btn-block btn-lg" id="btn-step1">Continue to Payment</button>
  </div>

  <!-- Step 2: Payment (Simulated) -->
  <div class="checkout-step" id="checkout-step-2" style="display:none">
    <h2 style="margin-bottom:20px;font-size:20px">Payment Method</h2>
    <p style="color:var(--muted);margin-bottom:16px">Secure payment. Enter your payment details below.</p>
    <div class="form-group">
      <label for="card-number">Card Number</label>
      <input type="text" id="card-number" placeholder="4242 4242 4242 4242" maxlength="19">
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="form-group">
        <label for="expiry">Expiry Date</label>
        <input type="text" id="expiry" placeholder="MM/YY" maxlength="5">
      </div>
      <div class="form-group">
        <label for="cvc">CVC</label>
        <input type="text" id="cvc" placeholder="123" maxlength="4">
      </div>
    </div>
    {coupon_html}
    {order_summary_html}
    <div style="display:flex;gap:12px">
      <button class="btn btn-secondary" onclick="document.querySelectorAll('.checkout-step').forEach(function(s){{s.style.display='none'}});document.getElementById('checkout-step-1').style.display='block'">Back</button>
      <button class="btn btn-primary btn-block btn-lg" id="btn-step2">Review Order</button>
    </div>
  </div>

  <!-- Step 3: Review & Place Order -->
  <div class="checkout-step" id="checkout-step-3" style="display:none">
    <h2 style="margin-bottom:20px;font-size:20px">Review Your Order</h2>
    <div id="review-summary">
      <p style="color:var(--muted)">Loading review...</p>
    </div>
    <div style="margin-top:16px;padding:16px;background:#fef3c7;border-radius:var(--radius);font-size:13px;color:#92400e">
      
    </div>
    <div style="display:flex;gap:12px;margin-top:20px">
      <button class="btn btn-secondary" onclick="document.querySelectorAll('.checkout-step').forEach(function(s){{s.style.display='none'}});document.getElementById('checkout-step-2').style.display='block'">Back</button>
      <button class="btn btn-primary btn-block btn-lg" id="btn-place-order">Place Order</button>
    </div>
  </div>

</main>

<footer class="footer">
  <div class="footer-bottom">
    <p>{_esc(footer_text)}</p>
  </div>
</footer>

{_foot()}"""
    return html


# ---------------------------------------------------------------------------
# 6. render_order_page
# ---------------------------------------------------------------------------
def render_order_page(design, brand_kit):
    """Generate order confirmation page — reads ?order= param."""
    brand_name = _safe_str(brand_kit.get("brand_name") or brand_kit.get("name", ""), "Store")
    footer_text = _get_footer_text(brand_kit)

    html = f"""{_head(f"Order Confirmed - {brand_name}")}
<header class="header">
  <div class="logo"><a href="/">{_esc(brand_name)}</a></div>
  <nav class="nav">
    <a href="/">Home</a>
    <a href="/about/">About</a>
    <a href="/contact/">Contact</a>
  </nav>
</header>

<main class="order-confirmation">
  <div class="check-icon">&#10003;</div>
  <h1>Order Confirmed!</h1>
  <p>Thank you for your purchase. Your order has been placed successfully.</p>
  <div class="order-id">Order #<span id="order-number">---</span></div>
  <p>A confirmation email will be sent shortly.</p>
  <p style="margin-top:32px">
    <a href="/" class="btn btn-primary">Continue Shopping</a>
  </p>
</main>

<footer class="footer">
  <div class="footer-bottom">
    <p>{_esc(footer_text)}</p>
  </div>
</footer>

{_foot()}"""
    return html


# ---------------------------------------------------------------------------
# 7. render_policy_pages
# ---------------------------------------------------------------------------
def render_policy_pages(design, brand_kit):
    """Return dict {filename: html_content} for policy/info pages.

    Keys: about/, contact/, privacy.html, terms.html, shipping.html, returns.html, faq.html
    """
    brand_name = _safe_str(brand_kit.get("brand_name") or brand_kit.get("name", ""), "Store")
    footer_text = _get_footer_text(brand_kit)

    business = brand_kit.get("business_info", {})
    if isinstance(business, str):
        try:
            business = json.loads(business)
        except (json.JSONDecodeError, TypeError):
            business = {}
    biz_address = business.get("address", "123 Commerce St, Suite 100, New York, NY 10001")
    biz_phone = business.get("phone", "+1 (555) 123-4567")
    biz_email = business.get("email", "support@example.com")

    def _page(title, body):
        return f"""{_head(f"{title} - {brand_name}")}
<header class="header">
  <div class="logo"><a href="/">{_esc(brand_name)}</a></div>
  <nav class="nav">
    <a href="/">Home</a>
    <a href="/about/">About</a>
    <a href="/contact/">Contact</a>
  </nav>
</header>

<main class="page-content">
  <h1>{_esc(title)}</h1>
  {body}
</main>

<footer class="footer">
  <div class="footer-grid">
    <div class="footer-col">
      <h4>Shop</h4>
      <a href="/">All Products</a>
      <a href="/about/">About Us</a>
      <a href="/contact/">Contact</a>
    </div>
    <div class="footer-col">
      <h4>Support</h4>
      <a href="/shipping/">Shipping</a>
      <a href="/returns/">Returns</a>
      <a href="/faq/">FAQ</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy/">Privacy Policy</a>
      <a href="/terms/">Terms of Service</a>
    </div>
    <div class="footer-col">
      <h4>Contact</h4>
      <a href="/contact/">Get in Touch</a>
      <p style="color:rgba(255,255,255,0.5);font-size:13px;margin-top:8px">We're here to help</p>
    </div>
  </div>
  <div class="footer-bottom">
    <p>{_esc(footer_text)}</p>
  </div>
</footer>

{_foot()}"""

    files = {}

    files["about/index.html"] = _page("About Us", f"""
<p>Welcome to {_esc(brand_name)}, your trusted destination for quality products.</p>
<p>We are committed to providing exceptional value and outstanding customer service. Our team carefully curates every product in our collection to ensure it meets the highest standards of quality and design.</p>
<h2>Our Mission</h2>
<p>To make premium products accessible to everyone, with fast shipping, fair prices, and a seamless shopping experience.</p>
<h2>Why Shop With Us</h2>
<p><strong>Quality First:</strong> Every product is vetted for durability and performance.</p>
<p><strong>Customer Focus:</strong> Our support team is here for you 24/7.</p>
<p><strong>Fast Shipping:</strong> We process orders quickly and ship worldwide.</p>
<p><strong>Secure Shopping:</strong> Your data is always protected with industry-standard encryption.</p>
""")

    files["contact/index.html"] = _page("Contact Us", f"""
<p>We would love to hear from you! Reach out to us using any of the methods below.</p>
<div style="background:#f9fafb;padding:24px;border-radius:var(--radius);margin-top:20px;border:1px solid var(--border)">
  <p><strong>Address:</strong><br>{_esc(biz_address)}</p>
  <p style="margin-top:12px"><strong>Phone:</strong><br>{_esc(biz_phone)}</p>
  <p style="margin-top:12px"><strong>Email:</strong><br><a href="mailto:{_esc(biz_email)}">{_esc(biz_email)}</a></p>
</div>
<h2 style="margin-top:32px">Business Hours</h2>
<p>Monday - Friday: 9:00 AM - 6:00 PM (EST)</p>
<p>Saturday: 10:00 AM - 4:00 PM (EST)</p>
<p>Sunday: Closed</p>
<p style="margin-top:20px">We typically respond to all inquiries within 24 hours.</p>
""")

    files["privacy/index.html"] = _page("Privacy Policy", f"""
<p>Last Updated: January 2025</p>
<p>{_esc(brand_name)} ("we", "our", or "us") is committed to protecting your privacy. This Privacy Policy explains how we collect, use, disclose, and safeguard your information when you visit our website.</p>
<h2>Information We Collect</h2>
<p><strong>Personal Data:</strong> When you make a purchase or create an account, we may collect your name, email address, shipping address, and phone number.</p>
<p><strong>Payment Information:</strong> Payment details are processed securely by our payment partners. We do not store your full credit card information on our servers.</p>
<p><strong>Usage Data:</strong> We automatically collect information about how you interact with our site, including pages visited, time spent, and referring URLs.</p>
<h2>How We Use Your Information</h2>
<p>We use the information we collect to process your orders, communicate with you about your purchases, improve our website and services, and send promotional communications (with your consent).</p>
<h2>Data Protection</h2>
<p>We implement a variety of security measures to maintain the safety of your personal information. All sensitive data is transmitted via Secure Socket Layer (SSL) technology.</p>
<h2>Your Rights</h2>
<p>You have the right to access, correct, or delete your personal data at any time. To exercise these rights, please contact us at <a href="mailto:{_esc(biz_email)}">{_esc(biz_email)}</a>.</p>
<h2>Contact</h2>
<p>If you have questions about this Privacy Policy, please contact us at <a href="mailto:{_esc(biz_email)}">{_esc(biz_email)}</a>.</p>
""")

    files["terms/index.html"] = _page("Terms of Service", f"""
<p>Last Updated: January 2025</p>
<p>By accessing or using the {_esc(brand_name)} website, you agree to be bound by these Terms of Service.</p>
<h2>Use of the Site</h2>
<p>You may use our site for lawful purposes only. You agree not to reproduce, duplicate, copy, sell, or exploit any portion of the site without our express written permission.</p>
<h2>Products and Pricing</h2>
<p>All prices are listed in USD and are subject to change without notice. We reserve the right to modify or discontinue any product at any time. We strive to display product colors and images accurately but cannot guarantee that your device's display will be accurate.</p>
<h2>Orders and Payment</h2>
<p>By placing an order, you agree to provide current, complete, and accurate purchase information. We reserve the right to refuse or cancel any order for any reason.</p>
<h2>Intellectual Property</h2>
<p>All content on this site, including text, graphics, logos, images, and software, is the property of {_esc(brand_name)} and is protected by copyright laws.</p>
<h2>Limitation of Liability</h2>
<p>{_esc(brand_name)} shall not be liable for any indirect, incidental, special, or consequential damages arising from your use of the site or products purchased.</p>
<h2>Changes to Terms</h2>
<p>We reserve the right to update these terms at any time. Changes will be effective immediately upon posting to this page.</p>
""")

    files["shipping/index.html"] = _page("Shipping Policy", f"""
<h2>Shipping Destinations</h2>
<p>We ship to most countries worldwide. If you are unsure whether we ship to your location, please contact us at <a href="mailto:{_esc(biz_email)}">{_esc(biz_email)}</a> before placing your order.</p>
<h2>Processing Time</h2>
<p>Orders are typically processed within 1-2 business days after payment confirmation. During peak seasons, processing may take up to 3 business days.</p>
<h2>Shipping Methods and Delivery Times</h2>
<p><strong>Standard Shipping:</strong> 5-10 business days (domestic), 10-20 business days (international)</p>
<p><strong>Express Shipping:</strong> 2-4 business days (domestic), 5-8 business days (international)</p>
<h2>Shipping Rates</h2>
<p><strong>Free Standard Shipping</strong> on all orders over $50.</p>
<p>Orders under $50: Flat rate of $4.99 (domestic) or $9.99 (international) for standard shipping.</p>
<p>Express shipping rates are calculated at checkout based on weight and destination.</p>
<h2>Tracking</h2>
<p>Once your order ships, you will receive a confirmation email with tracking information. Please allow 24 hours for tracking to update.</p>
<h2>Customs and Duties</h2>
<p>International orders may be subject to customs fees, import duties, or taxes. These charges are the responsibility of the recipient and are not included in the item price or shipping cost.</p>
""")

    files["returns/index.html"] = _page("Returns Policy", f"""
<h2>Return Window</h2>
<p>We accept returns within 30 days of delivery. After 30 days, unfortunately we cannot offer a refund or exchange.</p>
<h2>Return Conditions</h2>
<p>To be eligible for a return, your item must be:</p>
<p>- Unused and in the same condition that you received it</p>
<p>- In the original packaging</p>
<p>- Accompanied by the receipt or proof of purchase</p>
<h2>Non-Returnable Items</h2>
<p>Certain items cannot be returned, including:</p>
<p>- Gift cards</p>
<p>- Downloadable software products</p>
<p>- Personal care items (for hygiene reasons)</p>
<h2>How to Initiate a Return</h2>
<p>To start a return, please contact us at <a href="mailto:{_esc(biz_email)}">{_esc(biz_email)}</a> with your order number and the reason for the return. We will provide you with return shipping instructions.</p>
<h2>Refunds</h2>
<p>Once we receive and inspect your returned item, we will notify you of the approval or rejection of your refund. If approved, your refund will be processed to your original payment method within 5-10 business days.</p>
<h2>Return Shipping</h2>
<p>Return shipping costs are the responsibility of the customer unless the return is due to our error (defective item, wrong item shipped, etc.).</p>
<h2>Exchanges</h2>
<p>We only replace items if they are defective or damaged. If you need to exchange an item, please contact us at <a href="mailto:{_esc(biz_email)}">{_esc(biz_email)}</a>.</p>
""")

    files["faq/index.html"] = _page("FAQ", f"""
<p>Find answers to the most common questions below. If you cannot find what you are looking for, visit our <a href="/contact/">Contact page</a> to get in touch.</p>
<details class="faq-item"><summary>What payment methods do you accept?</summary><div class="faq-answer"><p>We accept all major credit cards (Visa, Mastercard, American Express), PayPal, and Apple Pay. All payments are processed securely.</p></div></details>
<details class="faq-item"><summary>How long does shipping take?</summary><div class="faq-answer"><p>Standard shipping takes 5-10 business days domestically and 10-20 business days internationally. Express shipping options are available at checkout.</p></div></details>
<details class="faq-item"><summary>Can I change or cancel my order?</summary><div class="faq-answer"><p>You can change or cancel your order within 1 hour of placing it. After that, the order may have already been processed. Please contact us immediately for assistance.</p></div></details>
<details class="faq-item"><summary>Do you ship internationally?</summary><div class="faq-answer"><p>Yes, we ship to most countries worldwide. International shipping rates and delivery times vary by location.</p></div></details>
<details class="faq-item"><summary>How do I track my order?</summary><div class="faq-answer"><p>Once your order ships, you will receive an email with a tracking number. You can use this number on our carrier's website to track your package.</p></div></details>
<details class="faq-item"><summary>Is my personal information secure?</summary><div class="faq-answer"><p>Yes. We use industry-standard SSL encryption to protect all data transmitted through our website. We never sell or share your personal information with third parties.</p></div></details>
<details class="faq-item"><summary>What if I receive a damaged item?</summary><div class="faq-answer"><p>If you receive a damaged or defective item, please contact us within 48 hours of delivery with photos of the damage. We will arrange a replacement or full refund.</p></div></details>
""")

    return files


# ---------------------------------------------------------------------------
# 8. render_site — main entry point
# ---------------------------------------------------------------------------
# Essential pages for Stitch/Agnes generation (GMC review requirements)
DESIGN_PAGES = ["home", "product", "cart", "checkout",
                 "about", "contact", "privacy", "returns"]
# Extra pages: always use built-in CSS fallback
EXTRA_PAGES = ["order", "faq", "terms", "shipping"]

def _render_page_by_type(page_type, design, brand_kit, products):
    """Fallback: render a page using built-in CSS when Agnes didn't generate it."""
    if page_type == "home":
        return render_homepage(products, design, brand_kit)
    elif page_type == "cart":
        return render_cart_page(design, brand_kit)
    elif page_type == "checkout":
        return render_checkout_page(design, brand_kit)
    elif page_type == "order":
        return render_order_page(design, brand_kit)
    elif page_type in ("about", "contact", "faq", "privacy", "terms", "shipping", "returns"):
        pages = render_policy_pages(design, brand_kit)
        return pages.get(f"{page_type}/index.html", "")
    return ""


def try_stitch_design(brand_kit, progress_callback=None):
    """Try to generate store design via Google Stitch (TEXT_TO_UI flash model)."""
    brand_name = brand_kit.get("brand_name") or brand_kit.get("name", "Store")
    try:
        from stitch_client import StitchClient
        stitch = StitchClient()
        if not stitch.is_authenticated:
            if progress_callback: progress_callback("Stitch未认证，跳过...")
            return None
        if progress_callback: progress_callback(f"Stitch Flash正在为 {brand_name} 生成设计...")
        result = stitch.generate_store_design(
            brand_kit=brand_kit, pages=DESIGN_PAGES, progress_callback=progress_callback,
        )
        if result and isinstance(result, dict):
            screens = result.get("screens", {})
            project_id = result.get("project_id", "")
            screen_ids = result.get("screen_ids", {})
            if screens and len(screens) >= 2:
                if progress_callback: progress_callback(f"Stitch完成 {len(screens)} 页")
                logger.info(f"Stitch design for {brand_name}: {list(screens.keys())}")
                # Save project_id + screen_ids for reuse
                if project_id and brand_kit.get("id"):
                    try:
                        import json as _json
                        from models import update_brand_kit
                        update_brand_kit(brand_kit["id"], {
                            "design_project_id": project_id,
                            "design_screens": _json.dumps(screen_ids) if screen_ids else "{}",
                        })
                    except Exception:
                        pass
                # Attach project_id so caller can fetch DESIGN.md
                screens["_project_id"] = project_id
                return screens
        if progress_callback: progress_callback("Stitch不足，尝试Agnes...")
        return None
    except Exception as e:
        if progress_callback: progress_callback(f"Stitch失败: {str(e)[:40]}")
        logger.warning(f"Stitch failed for {brand_name}: {e}")
        return None


_LINK_TEXT_MAP = {
    "home": "index.html", "shop": "index.html", "store": "index.html",
    "products": "index.html", "product": "index.html", "catalog": "index.html",
    "cart": "cart/index.html", "bag": "cart.html", "basket": "cart.html",
    "checkout": "checkout/index.html",
    "order": "order/index.html", "orders": "order.html", "track order": "order.html",
    "about": "about/index.html", "about us": "about.html", "our story": "about.html",
    "contact": "contact/index.html", "contact us": "contact.html", "support": "contact.html",
    "faq": "faq/index.html", "help": "faq.html",
    "privacy": "privacy/index.html", "privacy policy": "privacy.html",
    "terms": "terms/index.html", "terms of service": "terms.html", "terms & conditions": "terms.html",
    "shipping": "shipping/index.html", "shipping info": "shipping.html", "delivery": "shipping.html",
    "returns": "returns/index.html", "returns & refunds": "returns.html", "refund": "returns.html",
    "account": "#", "login": "#", "sign in": "#", "register": "#", "sign up": "#",
    "search": "#", "wishlist": "#",
}

def _fix_page_links(html, current_page, page_map):
    """Replace # links in generated HTML with correct page URLs.
    Handles text links, logo links (img inside a), and icon-only links (aria-label)."""
    import re
    def _replace_href(match):
        full_tag = match.group(0)
        inner = match.group(1)
        text = re.sub(r"<[^>]+>", "", inner).strip().lower()

        # Detect logo/home links: contains img, or class/id contains "logo" or "brand"
        is_logo = bool(re.search(r'<img\s', inner, re.I)) or bool(re.search(r'class=["\'][^"\']*logo|id=["\'][^"\']*logo|class=["\'][^"\']*brand', full_tag, re.I))

        if is_logo:
            target = "index.html"
        else:
            target = None
            for keyword, filename in _LINK_TEXT_MAP.items():
                if keyword in text:
                    target = filename
                    break

        if target and target != page_map.get(current_page, ""):
            # Replace any empty/placeholder href
            full_tag = re.sub(r"""href=["'](?:#|javascript:void\(0\)|)["']""", 'href="' + target + '"', full_tag, count=1)
        return full_tag

    # Match <a> tags with empty or # href
    html = re.sub(
        r"""<a\s[^>]*href=["'](?:#|javascript:void\(0\)|)["'][^>]*>(.*?)</a>""",
        _replace_href, html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return html


def _extract_colors_from_md(md_text):
    """Extract color hex codes from DESIGN.md markdown."""
    import re
    colors = re.findall(r"#[0-9a-fA-F]{6}", md_text[:3000])
    return list(dict.fromkeys(colors))[:2] if len(colors) >= 2 else None


def _extract_dominant_colors(html):
    """Extract CSS --primary and --accent colors from Stitch HTML for CSS fallback consistency."""
    import re
    colors = []
    for var in ["--primary", "--accent", "--bg", "--text"]:
        m = re.search(rf"{var}\s*:\s*(#[0-9a-fA-F]{{3,6}})", html)
        if m:
            colors.append(m.group(1))
    # Fallback: grab first two hex colors from :root or style blocks
    if len(colors) < 2:
        hexes = re.findall(r"#[0-9a-fA-F]{6}", html[:5000])
        colors = list(dict.fromkeys(hexes))[:2]  # deduplicate, take 2
    return colors if len(colors) >= 2 else None


def _is_valid_html(html):
    """Check if HTML is a valid page (not just an SVG/icon/image)."""
    if not html or len(html) < 500:
        return False
    lower = html.lower()
    if "<html" not in lower and "<!doctype" not in lower:
        return False
    import re
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    body = body_match.group(1) if body_match else html
    body_text = re.sub(r"<[^>]+>", " ", body).strip()
    if body.strip().startswith("<svg") and "</svg>" in body[:500] and len(body_text) < 100:
        return False
    has_structure = any(tag in lower for tag in ["<nav", "<header", "<footer", "<main", "<h1", "<h2", "<a href", "<button", "<form", "<input"])
    if not has_structure and len(body_text) < 200:
        return False
    return True


def _inject_products_into_homepage(html, products, design, brand_kit):
    """Inject real product cards into Stitch homepage product grid.
    Extracts the first product card HTML as a template from Stitch's output,
    then replaces all placeholder cards with real product data."""
    import re

    # Find the product grid div (Tailwind grid with gap-lg)
    grid_match = re.search(
        r'<div\s+class="[^"]*grid[^"]*grid-cols[^"]*gap-lg[^"]*"[^>]*>',
        html, re.IGNORECASE
    )
    if not grid_match:
        return html

    # Extract the first product card HTML as template
    grid_content = html[grid_match.end():]
    # Find the first card comment marker
    card_comment = re.search(r'<!--\s*Product Card 1\s*-->', grid_content)
    if card_comment:
        card_start = card_comment.end()
        # Find the next card comment or grid closing
        next_card = re.search(r'<!--\s*Product Card \d+\s*-->', grid_content[card_start:])
        if next_card:
            card_template = grid_content[card_start:card_start + next_card.start()].strip()
        else:
            # Last card - find closing </div> of grid
            card_template = grid_content[card_start:].strip()

    # Build real product cards
    cards_html = ""
    for p in products:
        pid = p.get("id", "")
        title = _esc(p.get("title", "Product"))
        price_val = p.get("price", "0")
        image = p.get("images", "") or p.get("image_url", "")
        if isinstance(image, list):
            image = image[0] if image else ""
        elif isinstance(image, str) and image.startswith("["):
            try:
                image = json.loads(image)[0]
            except Exception:
                pass
        elif isinstance(image, str) and "|" in image:
            image = image.split("|")[0]

        if card_template:
            card_html = card_template
            # Replace image src and alt
            card_html = re.sub(r'src="[^"]*"', f'src="{_esc(image)}"', card_html, count=1)
            card_html = re.sub(r'data-alt="[^"]*"', f'data-alt="{_esc(title)}"', card_html, count=1)
            card_html = re.sub(r'alt="[^"]*"', f'alt="{_esc(title)}"', card_html, count=1)
            # Replace product title text
            card_html = re.sub(r'>[^<]*</h\d>', f'>{_esc(title)}</h3>', card_html, count=1)
            # Replace price text
            card_html = re.sub(r'>\$[^<]*<', f'>\${_esc(str(price_val))}<', card_html, count=1)
            # Add link wrapper
            card_html = f'<a href="/products/{pid}/">{card_html}</a>'
            cards_html += f'<!-- Product Card {pid} -->\n{card_html}\n'
        else:
            # Fallback simple card
            cards_html += f'<div class="group cursor-pointer"><a href="/products/{pid}/"><div><img src="{_esc(image)}" alt="{_esc(title)}"></div><h3>{_esc(title)}</h3><p>\${_esc(str(price_val))}</p></a></div>\n'

    # Replace grid content: from first Product Card marker to grid closing </div>
    # Find the first card marker position
    first_card = re.search(r'<!--\s*Product Card 1\s*-->', html[grid_match.end():])
    if first_card:
        replace_start = grid_match.end() + first_card.start()
    else:
        replace_start = grid_match.end()

    # Find grid closing </div>
    depth = 0
    close_pos = replace_start
    for m in re.finditer(r'</div>|<div[^>]*>', html[replace_start:], re.IGNORECASE):
        if m.group().startswith('</div'):
            if depth == 0:
                close_pos = replace_start + m.start()
                break
            depth -= 1
        else:
            depth += 1

    if close_pos > replace_start:
        html = html[:replace_start] + '\n' + cards_html + '\n' + html[close_pos:]
    return html


def render_site_to_dict(domain, brand_kit, products, progress_callback=None):
    """Same as render_site but returns dict {filename: content} without writing to disk.
    Tries Agnes AI first; falls back to built-in CSS."""
    brand_name = brand_kit.get("brand_name") or brand_kit.get("name", domain)
    design = _load_design(brand_kit)
    global _INLINE_CSS, _INLINE_JS

    # Try Stitch Flash → built-in CSS
    design_pages = try_stitch_design(brand_kit, progress_callback)

    if design_pages and design_pages.get("home"):
        logger.info(f"Using Agnes design for {domain} ({len(design_pages)} pages)")

        # Extract colors from Stitch DESIGN.md or pages so CSS fallback matches
        try:
            from stitch_client import StitchClient
            stitch2 = StitchClient()
            if stitch2.is_authenticated:
                project_id = design_pages.get("_project_id") or brand_kit.get("design_project_id", "")
                if project_id:
                    spec = stitch2.get_design_spec(project_id)
                    if spec:
                        extracted = _extract_colors_from_md(spec)
                        if extracted:
                            brand_kit = dict(brand_kit)
                            brand_kit["colors"] = extracted
        except Exception:
            pass
        if not brand_kit.get("colors") or len(brand_kit.get("colors", [])) < 2:
            for pt in ["about", "contact", "privacy", "returns", "cart", "checkout", "product"]:
                if design_pages.get(pt):
                    extracted = _extract_dominant_colors(design_pages[pt])
                    if extracted:
                        brand_kit = dict(brand_kit)
                        brand_kit["colors"] = extracted
                        break

        _INLINE_CSS = build_css(design, brand_kit)
        _INLINE_JS = STORE_JS

        result = {}
        # Map design pages to filenames
        page_map = {
            "home": "index.html", "cart": "cart/index.html", "checkout": "checkout/index.html",
            "order": "order/index.html", "about": "about/index.html", "contact": "contact/index.html",
            "faq": "faq/index.html", "privacy": "privacy/index.html", "terms": "terms/index.html",
            "shipping": "shipping/index.html", "returns": "returns/index.html",
        }
        for page_type, filename in page_map.items():
            if design_pages.get(page_type):
                html = design_pages[page_type]
                # Validate: skip SVG/icon-only responses
                if not _is_valid_html(html):
                    logger.warning(f"Stitch {page_type}: invalid content, using CSS fallback")
                    result[filename] = _render_page_by_type(page_type, design, brand_kit, products)
                else:
                    result[filename] = _fix_page_links(html, page_type, page_map)
            else:
                # Fallback to built-in for missing pages
                result[filename] = _render_page_by_type(page_type, design, brand_kit, products)

        # Inject real products into Stitch homepage product grid
        if products and len(products) > 0 and result.get("index.html"):
            logger.info(f"Injecting {len(products)} products into Stitch homepage for {domain}")
            result["index.html"] = _inject_products_into_homepage(result["index.html"], products, design, brand_kit)

        # Product pages always use built-in
        for p in (products or []):
            pid = p.get("id", "")
            if pid:
                result[f"products/{pid}/index.html"] = render_product_page(p, design, brand_kit, products)
        return result

    # Fallback: try saved screens from previous Stitch generation first
    saved_pages = {}
    project_id = brand_kit.get("design_project_id", "")
    if project_id:
        try:
            from stitch_client import StitchClient
            sc = StitchClient()
            if sc.is_authenticated:
                raw = brand_kit.get("design_screens", "")
                saved_ids = json.loads(raw) if raw and raw.strip() else {}
                if saved_ids:
                    if progress_callback: progress_callback("Stitch失败，尝试复用已保存页面...")
                    for page_type, sid in saved_ids.items():
                        html = sc.get_screen_code(f"projects/{project_id}/screens/{sid}")
                        if html and _is_valid_html(html):
                            saved_pages[page_type] = html
                            if progress_callback: progress_callback(f"复用已保存: {page_type}")
                    if saved_pages:
                        logger.info(f"Reused {len(saved_pages)} saved screens for {domain}")
        except Exception as e:
            logger.warning(f"Failed to reuse saved screens: {e}")

    # Build pages: saved screens first, CSS fallback for missing
    _INLINE_CSS = build_css(design, brand_kit)
    _INLINE_JS = STORE_JS
    result = {}
    page_map = {
        "home": "index.html", "cart": "cart/index.html", "checkout": "checkout/index.html",
        "order": "order/index.html", "about": "about/index.html", "contact": "contact/index.html",
        "faq": "faq/index.html", "privacy": "privacy/index.html", "terms": "terms/index.html",
        "shipping": "shipping/index.html", "returns": "returns/index.html",
    }
    for page_type, filename in page_map.items():
        if saved_pages.get(page_type):
            result[filename] = _fix_page_links(saved_pages[page_type], page_type, page_map)
        else:
            result[filename] = _render_page_by_type(page_type, design, brand_kit, products)
    for p in (products or []):
        pid = p.get("id", "")
        if pid:
            result[f"products/{pid}/index.html"] = render_product_page(p, design, brand_kit, products)
    return result

def render_site(domain, brand_kit, products, site_dir=None):
    """Render all static site files and write to disk.

    Args:
        domain (str): The domain name (used for site_dir default).
        brand_kit (dict): Brand kit data including design_system.
        products (list): List of product dicts from DB.
        site_dir (str|None): Output directory. Defaults to /app/backend/static-sites/{domain}.

    Returns:
        int: Total number of files written.
    """
    if site_dir is None:
        site_dir = f"/app/backend/static-sites/{domain}"

    assets_dir = os.path.join(site_dir, "assets")
    products_dir = os.path.join(site_dir, "products")
    os.makedirs(assets_dir, exist_ok=True)
    os.makedirs(products_dir, exist_ok=True)

    design = _load_design(brand_kit)
    files_written = 0

    # Set inline CSS/JS globals (inline into every page, no external assets)
    global _INLINE_CSS, _INLINE_JS
    _INLINE_CSS = build_css(design, brand_kit)
    _INLINE_JS = STORE_JS

    # 1. Homepage
    index_html = render_homepage(products, design, brand_kit)
    index_path = os.path.join(site_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_html)
    files_written += 1
    logger.info(f"Written: {index_path}")

    # 4. Product pages
    for p in products:
        pid = p.get("id", "")
        if not pid:
            continue
        product_html = render_product_page(p, design, brand_kit, products)
        product_path = os.path.join(products_dir, f"{pid}", "index.html")
        with open(product_path, "w", encoding="utf-8") as f:
            f.write(product_html)
        files_written += 1
    logger.info(f"Written: {len(products)} product pages")

    # 5. Cart page
    cart_html = render_cart_page(design, brand_kit)
    cart_path = os.path.join(site_dir, "cart", "index.html")
    with open(cart_path, "w", encoding="utf-8") as f:
        f.write(cart_html)
    files_written += 1

    # 6. Checkout page
    checkout_html = render_checkout_page(design, brand_kit)
    checkout_path = os.path.join(site_dir, "checkout", "index.html")
    with open(checkout_path, "w", encoding="utf-8") as f:
        f.write(checkout_html)
    files_written += 1

    # 7. Order confirmation page
    order_html = render_order_page(design, brand_kit)
    order_path = os.path.join(site_dir, "order", "index.html")
    with open(order_path, "w", encoding="utf-8") as f:
        f.write(order_html)
    files_written += 1

    # 8. Policy pages
    policy_files = render_policy_pages(design, brand_kit)
    for filename, content in policy_files.items():
        fpath = os.path.join(site_dir, filename)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        files_written += 1
    logger.info(f"Written: {len(policy_files)} policy pages")

    # 9. robots.txt
    robots_path = os.path.join(site_dir, "robots.txt")
    robots_content = f"User-agent: *\nAllow: /\nSitemap: https://{domain}/sitemap.xml\n"
    with open(robots_path, "w", encoding="utf-8") as f:
        f.write(robots_content)
    files_written += 1

    # 10. Copy brand assets (logo, favicon, og_image) if they exist
    for attr, dest_name in [("png_256", "logo.png"), ("ico", "favicon.ico"), ("og_image", "og-image.png")]:
        src = brand_kit.get(attr, "")
        if src and os.path.isfile(src):
            import shutil
            shutil.copy(src, os.path.join(assets_dir, dest_name))
            logger.info(f"Copied brand asset: {src} -> {os.path.join(assets_dir, dest_name)}")

    logger.info(f"render_site complete: {files_written} files written to {site_dir}")
    return files_written


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _get_footer_text(brand_kit):
    """Extract footer text from brand_kit footer_config or default."""
    footer = brand_kit.get("footer_config", {})
    if isinstance(footer, str):
        try:
            footer = json.loads(footer)
        except (json.JSONDecodeError, TypeError):
            footer = {}
    brand_name = _safe_str(brand_kit.get("brand_name") or brand_kit.get("name", ""), "Store")
    return footer.get("text", f"© 2025 {brand_name}. All rights reserved.")


def _darken(hex_color):
    """Darken a hex color by 15%. Simple linear reduction."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return hex_color
    try:
        r = max(0, int(hex_color[0:2], 16) - 40)
        g = max(0, int(hex_color[2:4], 16) - 40)
        b = max(0, int(hex_color[4:6], 16) - 40)
        return f"#{r:02x}{g:02x}{b:02x}"
    except ValueError:
        return hex_color


def _hex_to_rgb(hex_color):
    """Convert hex to 'r, g, b' string for rgba()."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return "0, 0, 0"
    try:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"{r}, {g}, {b}"
    except ValueError:
        return "0, 0, 0"


# ====================================================================
#  garden-skills 风格配方库 (ConardLi, MIT)
#  25 curated design recipes — we embed 8 representative ones
# ====================================================================

from garden_recipes import STYLE_RECIPES, DEFAULT_STYLE
