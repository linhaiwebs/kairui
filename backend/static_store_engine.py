"""Static store rendering engine — generates complete static e-commerce sites."""
import json
import os
import logging

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
    html+='</ul><div class="cart-drawer-total"><strong>Total: $'+cartTotal().toFixed(2)+'</strong></div><a href="/cart.html" class="btn btn-primary btn-block">View Cart</a>';
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
  if(summary){summary.innerHTML='<p>Subtotal: <strong>$'+cartTotal().toFixed(2)+'</strong></p><p>Items: '+cartCount()+'</p><a href="/checkout.html" class="btn btn-primary btn-block">Proceed to Checkout</a>';}
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
  window.location.href='/order.html?order='+orderId;
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
        ds = DEFAULT_DESIGN
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
    """Generate a complete style.css — taste-skill anti-slop design system.

    Taste-skill principles applied:
    - Full neutral palette generated from brand colors (not generic grays)
    - Inter font via Google Fonts CDN (professional, readable)
    - Modern card design: image-zoom hover + quick-add button
    - Intersection Observer scroll animations (no jQuery/animation libs)
    - Mobile: hamburger menu, bottom sticky cart bar
    - Anti-slop: no AI-purple gradients, no centered 3-card layouts
    - Proper spacing scale (4px base) and type hierarchy
    """
    colors = _brand_colors(brand_kit)
    primary = colors[0]
    accent = colors[1] if len(colors) > 1 else _lighten(primary, 0.2)
    bg = "#fafafa"
    text_color = "#1a1a2e"
    muted = "#6b7280"
    border_color = "#e5e7eb"
    radius = design.get("css_vars", {}).get("--radius", "12px")
    shadow_sm = "0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)"
    shadow_md = "0 4px 16px rgba(0,0,0,0.06), 0 1px 3px rgba(0,0,0,0.04)"
    shadow_lg = "0 20px 48px rgba(0,0,0,0.08), 0 4px 12px rgba(0,0,0,0.04)"
    transition_fast = "0.15s ease"
    transition_base = "0.25s cubic-bezier(0.4, 0, 0.2, 1)"

    # Generate 10-step neutral palette from primary color
    primary_hsl = _hex_to_hsl(primary)
    neutral_steps = {}
    for i, name in enumerate(["50","100","200","300","400","500","600","700","800","900"], start=1):
        l = 98 - i * 8  # 98% down to ~26%
        s = max(2, 8 - abs(i - 5))  # peak saturation in middle
        neutral_steps[name] = f"hsl({primary_hsl[0]}, {s}%, {l}%)"

    # Layout config
    layout = design.get("layout", {})
    nav_cfg = layout.get("nav", {})
    nav_style = "sticky" if nav_cfg.get("style") != "static" else "relative"

    product_card = design.get("product_card", {})
    cols = product_card.get("columns_desktop", 4)
    card_style = product_card.get("style", "clean")
    hover = product_card.get("hover_effect", "image_zoom")
    show_badge = product_card.get("show_badge", True)
    components = set(design.get("components", []))
    cart_style = design.get("cart", {}).get("style", "drawer")
    footer_cols = layout.get("footer", {}).get("columns", 4)
    anim_level = design.get("animation_level", "subtle")

    # Card hover effects
    hover_css = ""
    if hover == "image_zoom":
        hover_css = ".product-card:hover .product-card-img{transform:scale(1.06)}.product-card:hover .quick-add{opacity:1;transform:translateY(0)}"
    elif hover == "lift":
        hover_css = ".product-card:hover{transform:translateY(-6px);box-shadow:" + shadow_lg + "}"
    elif hover == "glow":
        hover_css = ".product-card:hover{box-shadow:0 0 0 2px var(--primary),0 16px 40px rgba(0,0,0,0.1)}"

    # Scroll animation CSS + JS
    scroll_anim_css = ""
    scroll_anim_js = ""
    if anim_level != "none":
        scroll_anim_css = """.reveal{opacity:0;transform:translateY(24px);transition:opacity .6s cubic-bezier(.4,0,.2,1),transform .6s cubic-bezier(.4,0,.2,1)}.reveal.visible{opacity:1;transform:translateY(0)}"""
        scroll_anim_js = """"use strict";function ra(){var e=document.querySelectorAll(".reveal");if(!e.length)return;function t(){e.forEach(function(e){var t=e.getBoundingClientRect().top;t<window.innerHeight-80&&e.classList.add("visible")})}t(),window.addEventListener("scroll",t,{passive:!0})}document.addEventListener("DOMContentLoaded",ra);"""

    css = f'''/* Static Store — taste-skill design system */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {{
  --primary: {primary};
  --primary-dark: {_darken(primary)};
  --primary-light: {_lighten(primary, 0.15)};
  --accent: {accent};
  --accent-dark: {_darken(accent)};
  --bg: {bg};
  --surface: #ffffff;
  --text: {text_color};
  --text-secondary: #4b5563;
  --muted: {muted};
  --border: {border_color};
  --radius: {radius};
  --radius-sm: 8px;
  --radius-lg: 16px;
  --shadow-sm: {shadow_sm};
  --shadow-md: {shadow_md};
  --shadow-lg: {shadow_lg};
  --transition-fast: {transition_fast};
  --transition: {transition_base};
  --header-height: 68px;
}}

*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{scroll-behavior:smooth;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}}
body{{
  font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:var(--bg);color:var(--text);line-height:1.6;min-height:100vh;
  display:flex;flex-direction:column;font-size:16px;font-weight:400;
}}
img{{max-width:100%;height:auto;display:block}}
a{{color:var(--primary);text-decoration:none;transition:color var(--transition-fast)}}
a:hover{{color:var(--primary-dark)}}
button{{font-family:inherit;cursor:pointer}}

/* --- Container & Spacing --- */
.container{{max-width:1320px;margin:0 auto;padding:0 24px}}
.section{{padding:64px 0}}
@media(max-width:768px){{.section{{padding:40px 0}}.container{{padding:0 16px}}}}'''

    # --- Header ---
    css += f'''
/* --- Header --- */
.header{{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:0 24px;height:var(--header-height);display:flex;
  align-items:center;justify-content:space-between;
  position:{nav_style};top:0;z-index:100;width:100%;
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  background:rgba(255,255,255,0.92);
}}
.header .logo{{font-size:22px;font-weight:800;letter-spacing:-0.3px}}
.header .logo a{{color:var(--text);text-decoration:none;display:flex;align-items:center;gap:8px}}
.header .logo a:hover{{color:var(--primary)}}
.nav{{display:flex;align-items:center;gap:28px}}
.nav a{{color:var(--text-secondary);font-size:15px;font-weight:500;transition:color var(--transition-fast);text-decoration:none;position:relative}}
.nav a:hover,.nav a.active{{color:var(--text)}}
.nav a.active::after{{content:'';position:absolute;bottom:-4px;left:0;right:0;height:2px;background:var(--primary);border-radius:1px}}
.header-right{{display:flex;align-items:center;gap:16px}}
.cart-icon{{position:relative;color:var(--text);font-size:22px;cursor:pointer;background:none;border:none;padding:6px;border-radius:8px;transition:background var(--transition-fast)}}
.cart-icon:hover{{background:var(--bg)}}
.cart-count{{position:absolute;top:-2px;right:-4px;background:var(--primary);color:#fff;font-size:11px;font-weight:700;min-width:20px;height:20px;border-radius:10px;display:none;align-items:center;justify-content:center;line-height:1}}
.hamburger{{display:none;background:none;border:none;color:var(--text);font-size:24px;padding:4px;cursor:pointer}}
@media(max-width:768px){{
  .hamburger{{display:block}}
  .nav{{display:none;position:fixed;top:var(--header-height);left:0;right:0;background:var(--surface);flex-direction:column;padding:20px;gap:12px;box-shadow:var(--shadow-lg);z-index:99}}
  .nav.open{{display:flex}}
  .nav a{{font-size:17px;padding:8px 0}}
}}'''

    # --- Hero ---
    hero = layout.get("hero", {})
    hero_type = hero.get("type", "split")
    headline = hero.get("headline", "New Arrivals")
    subheadline = hero.get("subheadline", "Discover the collection")
    if hero_type == "minimal":
        css += f'''
/* --- Hero — Minimal --- */
.hero{{background:var(--surface);padding:80px 24px 48px;text-align:center}}
.hero h1{{font-size:clamp(36px,5vw,56px);font-weight:800;letter-spacing:-1.5px;margin-bottom:12px;color:var(--text);line-height:1.1}}
.hero p{{font-size:18px;color:var(--text-secondary);max-width:560px;margin:0 auto;line-height:1.7}}'''
    elif hero_type == "split":
        css += f'''
/* --- Hero — Split (image + text) --- */
.hero{{background:var(--surface);padding:72px 24px 56px;display:flex;align-items:center;justify-content:center;gap:64px;max-width:1200px;margin:0 auto}}
.hero-text{{flex:1;max-width:520px}}
.hero-text h1{{font-size:clamp(32px,4.5vw,52px);font-weight:800;letter-spacing:-1.2px;line-height:1.08;margin-bottom:16px;color:var(--text)}}
.hero-text p{{font-size:17px;color:var(--text-secondary);line-height:1.7;margin-bottom:28px}}
.hero-text .cta{{display:inline-flex;align-items:center;gap:8px;padding:14px 32px;background:var(--primary);color:#fff;border-radius:var(--radius);font-weight:600;font-size:15px;text-decoration:none;transition:background var(--transition-fast),transform var(--transition-fast)}}
.hero-text .cta:hover{{background:var(--primary-dark);transform:translateY(-1px);color:#fff}}
.hero-visual{{flex:1;display:flex;align-items:center;justify-content:center;min-height:320px;background:linear-gradient(135deg,var(--primary-light),var(--primary));border-radius:var(--radius-lg);color:#fff;font-size:72px}}
@media(max-width:768px){{.hero{{flex-direction:column;gap:32px;padding:48px 16px 32px;text-align:center}}.hero-text{{max-width:100%}}.hero-visual{{min-height:200px;width:100%}}}}'''
    else:
        css += f'''
/* --- Hero --- */
.hero{{background:linear-gradient(135deg,{primary}08,{accent}12);padding:72px 24px 56px;text-align:center}}
.hero h1{{font-size:clamp(32px,5vw,52px);font-weight:800;letter-spacing:-1.2px;margin-bottom:12px;color:var(--text);line-height:1.1}}
.hero p{{font-size:17px;color:var(--text-secondary);max-width:520px;margin:0 auto;line-height:1.7}}'''

    # --- Product Grid ---
    css += f'''
/* --- Product Grid --- */
.product-grid{{max-width:1320px;margin:0 auto;padding:48px 24px;display:grid;grid-template-columns:repeat({cols},1fr);gap:24px}}
@media(max-width:1024px){{.product-grid{{grid-template-columns:repeat(3,1fr)}}}}
@media(max-width:768px){{.product-grid{{grid-template-columns:repeat(2,1fr);gap:16px;padding:32px 16px}}}}
@media(max-width:480px){{.product-grid{{grid-template-columns:1fr}}}}'''

    # --- Product Card (taste-skill: image-first, quick-add, proper shadows) ---
    css += f'''
/* --- Product Card --- */
.product-card{{position:relative;border-radius:var(--radius);overflow:hidden;background:var(--surface);transition:transform var(--transition),box-shadow var(--transition);box-shadow:var(--shadow-sm);display:flex;flex-direction:column;group}}
.product-card:hover{{box-shadow:var(--shadow-md)}}
.product-card a{{color:inherit;text-decoration:none;display:flex;flex-direction:column;flex:1}}
.product-card-img-wrap{{position:relative;overflow:hidden;background:#f5f5f5;aspect-ratio:4/3}}
.product-card-img{{width:100%;height:100%;object-fit:cover;transition:transform .4s cubic-bezier(.4,0,.2,1)}}
.product-card:hover .product-card-img{{transform:scale(1.06)}}
.product-card-badge{{position:absolute;top:10px;left:10px;background:var(--primary);color:#fff;font-size:11px;font-weight:700;padding:4px 10px;border-radius:6px;letter-spacing:.3px;z-index:2}}
.quick-add{{position:absolute;bottom:12px;left:50%;transform:translateX(-50%) translateY(8px);opacity:0;background:var(--surface);color:var(--text);border:1px solid var(--border);padding:8px 18px;border-radius:20px;font-size:13px;font-weight:600;cursor:pointer;transition:all var(--transition);white-space:nowrap;box-shadow:var(--shadow-md);z-index:2}}
.product-card:hover .quick-add{{opacity:1;transform:translateX(-50%) translateY(0)}}
.quick-add:hover{{background:var(--primary);color:#fff;border-color:var(--primary)}}
.product-card-body{{padding:16px;flex:1;display:flex;flex-direction:column;gap:6px}}
.product-card-body h3{{font-size:14px;font-weight:600;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;color:var(--text)}}
.product-card-body .price{{font-size:16px;font-weight:700;color:var(--text);margin-top:auto}}
.product-card-body .price .original{{font-size:13px;color:var(--muted);text-decoration:line-through;font-weight:400;margin-left:6px}}
.product-card-rating{{font-size:12px;color:#f59e0b}}'''

    # --- Product Detail ---
    css += f'''
/* --- Product Detail --- */
.product-detail{{max-width:1200px;margin:40px auto;padding:0 24px;display:grid;grid-template-columns:1.1fr 1fr;gap:56px;align-items:start}}
@media(max-width:768px){{.product-detail{{grid-template-columns:1fr;gap:28px}}}}
.product-gallery .main-image{{width:100%;border-radius:var(--radius);background:#f5f5f5;object-fit:contain;max-height:560px;aspect-ratio:4/3}}
.product-gallery .thumbnails{{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap}}
.product-gallery .thumbnails img{{width:72px;height:72px;object-fit:cover;border-radius:8px;border:2px solid transparent;cursor:pointer;transition:border-color var(--transition-fast);background:#f5f5f5}}
.product-gallery .thumbnails img:hover,.product-gallery .thumbnails img.active{{border-color:var(--primary)}}
.product-info h1{{font-size:28px;font-weight:700;margin-bottom:8px;line-height:1.25;letter-spacing:-0.3px}}
.product-info .price{{font-size:26px;font-weight:700;color:var(--text);margin-bottom:20px}}
.product-info .price .original{{font-size:17px;color:var(--muted);text-decoration:line-through;font-weight:400;margin-left:8px}}
.product-info .sku{{font-size:13px;color:var(--muted);margin-bottom:4px;font-family:monospace}}
.product-info .description{{margin:20px 0;line-height:1.8;color:var(--text-secondary);font-size:15px}}
.add-to-cart{{display:inline-flex;align-items:center;gap:10px;padding:14px 36px;background:var(--primary);color:#fff;border:none;border-radius:var(--radius);font-size:16px;font-weight:600;cursor:pointer;transition:all var(--transition-fast)}}
.add-to-cart:hover{{background:var(--primary-dark);transform:translateY(-1px);box-shadow:var(--shadow-md)}}
.quantity-input{{display:flex;align-items:center;margin:20px 0}}
.quantity-input button{{width:44px;height:44px;border:1px solid var(--border);background:var(--surface);font-size:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background var(--transition-fast)}}
.quantity-input button:hover{{background:var(--bg)}}
.quantity-input button:first-child{{border-radius:var(--radius)0 0 var(--radius)}}
.quantity-input button:last-child{{border-radius:0 var(--radius)var(--radius)0}}
.quantity-input input{{width:60px;height:44px;border:1px solid var(--border);border-left:none;border-right:none;text-align:center;font-size:16px;font-weight:600}}'''

    # --- Breadcrumb ---
    css += f'''
/* --- Breadcrumb --- */
.breadcrumb{{max-width:1200px;margin:20px auto 0;padding:0 24px;font-size:13px;color:var(--muted)}}
.breadcrumb a{{color:var(--muted);text-decoration:none}}
.breadcrumb a:hover{{color:var(--primary)}}
.breadcrumb span{{margin:0 6px}}'''

    # --- Tabs ---
    css += f'''
/* --- Tabs --- */
.tabs{{max-width:1200px;margin:40px auto 0;padding:0 24px}}
.tab-nav{{display:flex;border-bottom:2px solid var(--border);gap:0;margin-bottom:28px}}
.tab-nav button{{padding:12px 24px;border:none;background:none;font-size:14px;font-weight:600;cursor:pointer;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-2px;transition:all var(--transition-fast)}}
.tab-nav button.active,.tab-nav button:hover{{color:var(--text);border-bottom-color:var(--primary)}}'''

    # --- Cart Drawer ---
    css += f'''
/* --- Cart Drawer --- */
.cart-drawer{{position:fixed;top:0;right:-420px;width:400px;max-width:100vw;height:100vh;background:var(--surface);box-shadow:var(--shadow-lg);z-index:200;transition:right var(--transition);display:flex;flex-direction:column}}
.cart-drawer.open{{right:0}}
.cart-overlay{{position:fixed;inset:0;background:rgba(0,0,0,.3);z-index:199;display:none}}
.cart-overlay.open{{display:block}}
.cart-header{{padding:20px 24px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}}
.cart-header h2{{font-size:18px;font-weight:700}}
.cart-items{{flex:1;overflow-y:auto;padding:16px 24px}}
.cart-item{{display:flex;gap:14px;padding:14px 0;border-bottom:1px solid var(--border)}}
.cart-item img{{width:72px;height:72px;object-fit:cover;border-radius:8px;background:#f5f5f5}}
.cart-item-info{{flex:1}}
.cart-item-info h4{{font-size:14px;font-weight:600;margin-bottom:4px}}
.cart-item-info .price{{font-size:14px;font-weight:700}}
.cart-item-remove{{background:none;border:none;color:var(--muted);cursor:pointer;font-size:18px;padding:4px}}
.cart-footer{{padding:20px 24px;border-top:1px solid var(--border)}}
.cart-total{{display:flex;justify-content:space-between;margin-bottom:14px;font-size:16px;font-weight:700}}
.checkout-btn{{display:block;width:100%;padding:14px;background:var(--primary);color:#fff;border:none;border-radius:var(--radius);font-size:16px;font-weight:600;text-align:center;cursor:pointer;transition:all var(--transition-fast);text-decoration:none}}
.checkout-btn:hover{{background:var(--primary-dark);color:#fff}}
@media(max-width:480px){{.cart-drawer{{width:100vw;right:-100vw}}}}'''

    # --- Footer ---
    css += f'''
/* --- Footer --- */
.footer{{background:var(--text);color:rgba(255,255,255,.7);padding:64px 24px 32px;margin-top:auto}}
.footer-grid{{max-width:1200px;margin:0 auto;display:grid;grid-template-columns:repeat({footer_cols},1fr);gap:40px;margin-bottom:40px}}
.footer-grid h3{{color:#fff;font-size:14px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;margin-bottom:16px}}
.footer-grid a{{color:rgba(255,255,255,.6);font-size:14px;display:block;margin-bottom:8px;transition:color var(--transition-fast)}}
.footer-grid a:hover{{color:#fff}}
.footer-grid p{{font-size:14px;line-height:1.7;margin-bottom:12px}}
.footer-bottom{{max-width:1200px;margin:0 auto;padding-top:24px;border-top:1px solid rgba(255,255,255,.1);font-size:13px;text-align:center}}
@media(max-width:768px){{.footer-grid{{grid-template-columns:repeat(2,1fr);gap:28px}}
@media(max-width:480px){{.footer-grid{{grid-template-columns:1fr}}}}}}'''

    # --- Trust Badges ---
    if "trust_badges" in components:
        css += f'''
/* --- Trust Badges --- */
.trust-badges{{max-width:1200px;margin:48px auto;padding:0 24px;display:flex;justify-content:center;flex-wrap:wrap;gap:32px}}
.trust-badges span{{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:500;color:var(--text-secondary)}}'''

    # --- Cart & Checkout Tables ---
    css += f'''
/* --- Cart Page --- */
.cart-page{{max-width:900px;margin:48px auto;padding:0 24px}}
.cart-page h1{{font-size:28px;font-weight:700;margin-bottom:28px;letter-spacing:-0.3px}}
.cart-table{{width:100%;border-collapse:collapse}}
.cart-table th{{text-align:left;padding:12px 0;border-bottom:2px solid var(--border);font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}}
.cart-table td{{padding:16px 0;border-bottom:1px solid var(--border);vertical-align:middle}}
.cart-table img{{width:64px;height:64px;object-fit:cover;border-radius:6px;background:#f5f5f5}}
.empty-state{{text-align:center;padding:80px 20px;color:var(--muted)}}
.empty-state svg{{width:64px;height:64px;margin-bottom:16px;opacity:.5}}
.empty-state p{{font-size:16px;margin-bottom:4px}}'''

    # --- Checkout & Forms ---
    css += f'''
/* --- Forms --- */
.form-group{{margin-bottom:20px}}
.form-group label{{display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:var(--text)}}
.form-group input,.form-group select,.form-group textarea{{width:100%;padding:12px 16px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:15px;font-family:inherit;background:var(--surface);transition:border-color var(--transition-fast)}}
.form-group input:focus,.form-group select:focus{{outline:none;border-color:var(--primary);box-shadow:0 0 0 3px {primary}18}}
.checkout-page{{max-width:700px;margin:48px auto;padding:0 24px}}
.checkout-page h1{{font-size:28px;font-weight:700;margin-bottom:28px;letter-spacing:-0.3px}}
.order-summary{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px 24px;margin-bottom:24px}}
.order-summary h3{{font-size:16px;font-weight:700;margin-bottom:12px}}
.order-item{{display:flex;justify-content:space-between;padding:8px 0;font-size:14px}}
.order-total{{display:flex;justify-content:space-between;padding:12px 0 0;border-top:1px solid var(--border);margin-top:8px;font-size:16px;font-weight:700}}'''

    # --- Toast ---
    css += f'''
/* --- Toast --- */
.toast-container{{position:fixed;top:20px;right:20px;z-index:300;display:flex;flex-direction:column;gap:8px}}
.toast{{padding:14px 20px;border-radius:var(--radius-sm);color:#fff;font-size:14px;font-weight:500;box-shadow:var(--shadow-lg);animation:slideIn .3s ease;max-width:360px}}
.toast-success{{background:#059669}}
.toast-error{{background:#dc2626}}
.toast-info{{background:var(--primary)}}
@keyframes slideIn{{from{{transform:translateX(100%);opacity:0}}to{{transform:translateX(0);opacity:1}}}}'''

    # --- Animations ---
    css += scroll_anim_css

    # Policy pages & FAQ
    css += f'''
/* --- Policy & FAQ --- */
.policy-page{{max-width:760px;margin:48px auto;padding:0 24px;line-height:1.8;font-size:15px}}
.policy-page h1{{font-size:28px;font-weight:700;margin-bottom:24px;letter-spacing:-0.3px}}
.policy-page h2{{font-size:20px;font-weight:700;margin:28px 0 12px}}
.policy-page p{{margin-bottom:14px;color:var(--text-secondary)}}
.faq-item{{margin-bottom:20px}}
.faq-item h3{{font-size:17px;font-weight:600;margin-bottom:8px}}
.faq-item p{{color:var(--text-secondary);line-height:1.7}}'''

    return css, scroll_anim_js

# ---------------------------------------------------------------------------
# 2. render_homepage
# ---------------------------------------------------------------------------
def render_homepage(products, design, brand_kit):
    """Generate the complete index.html with product grid."""
    brand_name = _safe_str(brand_kit.get("brand_name") or brand_kit.get("name", ""), "Store")
    colors = _brand_colors(brand_kit)
    primary = colors[0]
    accent = colors[1] if len(colors) > 1 else "#667eea"

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
        image = p.get("image_url", "")
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
            img_tag = f'<img src="{_esc(image)}" alt="{title}" class="product-card-img" loading="lazy" onerror="this.src=\'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22400%22 height=%22300%22><rect fill=%22%23f5f5f5%22 width=%22400%22 height=%22300%22/><text x=%2250%25%22 y=%2250%25%22 text-anchor=%22middle%22 dy=%22.3em%22 fill=%22%239ca3af%22 font-size=%2214%22>No Image</text></svg>\'">'
        else:
            img_tag = '<div class="product-card-img" style="background:#f5f5f5;display:flex;align-items:center;justify-content:center;color:#9ca3af;font-size:13px">No Image</div>'

        badge_html = ""
        if show_badge and is_on_sale:
            badge_html = '<div class="product-card-badge">Sale</div>'

        # Quick-add button
        quick_add = f'<button class="quick-add" onclick="event.preventDefault();addToCart({pid},\'{_esc(title)}\',{display_price or 0},\'{_esc(image)}\')">+ Quick Add</button>'

        cards += f"""<div class="product-card reveal">
  <a href="/products/{pid}.html">
    <div class="product-card-img-wrap">{badge_html}{img_tag}{quick_add}</div>
    <div class="product-card-body">
      <h3>{title}</h3>
      <div class="price">{price_html}</div>
    </div>
  </a>
</div>"""

    if not cards:
        cards = '<div class="empty-state"><svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg><p style="font-size:18px">Products coming soon!</p><p>Check back later for our new collection.</p></div>'

    trust_html = ""
    if "trust_badges" in components:
        trust_html = """<div class="trust-badges"><span>&#128274; Secure Checkout</span><span>&#128666; Free Shipping</span><span>&#128260; 30-Day Returns</span><span>&#128222; 24/7 Support</span></div>"""

    html = f"""{_head(f"{brand_name} - Shop")}
<header class="header">
  <div class="logo"><a href="/">{_esc(brand_name)}</a></div>
  <nav class="nav">
    <a href="/">Home</a>
    <a href="/about.html">About</a>
    <a href="/contact.html">Contact</a>
  </nav>
  <div class="header-right">
    <button class="cart-icon" id="cart-toggle" aria-label="Cart">
      &#128722;
      <span class="cart-count" id="cart-count">0</span>
    </button>
  </div>
</header>

<section class="hero">
  <h1>{_esc(headline)}</h1>
  <p>{_esc(subheadline)}</p>
</section>

{trust_html}

<main class="container" style="margin-top:40px;margin-bottom:40px">
  <div class="product-grid" id="products">
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
      <a href="/about.html">About Us</a>
      <a href="/contact.html">Contact</a>
    </div>
    <div class="footer-col">
      <h4>Support</h4>
      <a href="/shipping.html">Shipping</a>
      <a href="/returns.html">Returns</a>
      <a href="/faq.html">FAQ</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy.html">Privacy Policy</a>
      <a href="/terms.html">Terms of Service</a>
    </div>
    <div class="footer-col">
      <h4>Contact</h4>
      <a href="/contact.html">Get in Touch</a>
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
    image_url = product.get("image_url", "")

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
  <a href="/products/{rid}.html">
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
  <details class="faq-item"><summary>How can I contact support?</summary><div class="faq-answer"><p>Our support team is available 24/7. Visit our <a href="/contact.html">Contact page</a> for details.</p></div></details>
  <details class="faq-item"><summary>Are my payment details secure?</summary><div class="faq-answer"><p>Yes. All transactions are processed securely using industry-standard encryption.</p></div></details>
</section>"""

    # ---- Footer ----
    footer_text = _get_footer_text(brand_kit)

    html = f"""{_head(f"{title} - {brand_name}", extra=json_ld_script)}
<header class="header">
  <div class="logo"><a href="/">{_esc(brand_name)}</a></div>
  <nav class="nav">
    <a href="/">Home</a>
    <a href="/about.html">About</a>
    <a href="/contact.html">Contact</a>
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
      <a href="/about.html">About Us</a>
      <a href="/contact.html">Contact</a>
    </div>
    <div class="footer-col">
      <h4>Support</h4>
      <a href="/shipping.html">Shipping</a>
      <a href="/returns.html">Returns</a>
      <a href="/faq.html">FAQ</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy.html">Privacy Policy</a>
      <a href="/terms.html">Terms of Service</a>
    </div>
    <div class="footer-col">
      <h4>Contact</h4>
      <a href="/contact.html">Get in Touch</a>
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
    <a href="/about.html">About</a>
    <a href="/contact.html">Contact</a>
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
      <a href="/about.html">About Us</a>
      <a href="/contact.html">Contact</a>
    </div>
    <div class="footer-col">
      <h4>Support</h4>
      <a href="/shipping.html">Shipping</a>
      <a href="/returns.html">Returns</a>
      <a href="/faq.html">FAQ</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy.html">Privacy Policy</a>
      <a href="/terms.html">Terms of Service</a>
    </div>
    <div class="footer-col">
      <h4>Contact</h4>
      <a href="/contact.html">Get in Touch</a>
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
    <a href="/cart.html">Cart</a>
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
    <p style="color:var(--muted);margin-bottom:16px">This is a demo store. No real payment will be processed.</p>
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
      <strong>Demo Notice:</strong> This is a demonstration store. Clicking "Place Order" will simulate an order — no real charge will be made.
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
    <a href="/about.html">About</a>
    <a href="/contact.html">Contact</a>
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

    Keys: about.html, contact.html, privacy.html, terms.html, shipping.html, returns.html, faq.html
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
    <a href="/about.html">About</a>
    <a href="/contact.html">Contact</a>
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
      <a href="/about.html">About Us</a>
      <a href="/contact.html">Contact</a>
    </div>
    <div class="footer-col">
      <h4>Support</h4>
      <a href="/shipping.html">Shipping</a>
      <a href="/returns.html">Returns</a>
      <a href="/faq.html">FAQ</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy.html">Privacy Policy</a>
      <a href="/terms.html">Terms of Service</a>
    </div>
    <div class="footer-col">
      <h4>Contact</h4>
      <a href="/contact.html">Get in Touch</a>
      <p style="color:rgba(255,255,255,0.5);font-size:13px;margin-top:8px">We're here to help</p>
    </div>
  </div>
  <div class="footer-bottom">
    <p>{_esc(footer_text)}</p>
  </div>
</footer>

{_foot()}"""

    files = {}

    files["about.html"] = _page("About Us", f"""
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

    files["contact.html"] = _page("Contact Us", f"""
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

    files["privacy.html"] = _page("Privacy Policy", f"""
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

    files["terms.html"] = _page("Terms of Service", f"""
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

    files["shipping.html"] = _page("Shipping Policy", f"""
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

    files["returns.html"] = _page("Returns Policy", f"""
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

    files["faq.html"] = _page("FAQ", f"""
<p>Find answers to the most common questions below. If you cannot find what you are looking for, visit our <a href="/contact.html">Contact page</a> to get in touch.</p>
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
def render_site_to_dict(domain, brand_kit, products):
    """Same as render_site but returns dict {filename: content} without writing to disk."""
    design = _load_design(brand_kit)
    global _INLINE_CSS, _INLINE_JS
    _INLINE_CSS, _scroll_js = build_css(design, brand_kit)
    _INLINE_JS = _scroll_js + STORE_JS

    result = {}
    result["index.html"] = render_homepage(products, design, brand_kit)
    result["cart.html"] = render_cart_page(design, brand_kit)
    result["checkout.html"] = render_checkout_page(design, brand_kit)
    result["order.html"] = render_order_page(design, brand_kit)
    for fname, content in render_policy_pages(design, brand_kit).items():
        result[fname] = content
    for p in (products or []):
        pid = p.get("id", "")
        if pid:
            result[f"products/{pid}.html"] = render_product_page(p, design, brand_kit, products)
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
    _INLINE_CSS, _scroll_js = build_css(design, brand_kit)
    _INLINE_JS = _scroll_js + STORE_JS

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
        product_path = os.path.join(products_dir, f"{pid}.html")
        with open(product_path, "w", encoding="utf-8") as f:
            f.write(product_html)
        files_written += 1
    logger.info(f"Written: {len(products)} product pages")

    # 5. Cart page
    cart_html = render_cart_page(design, brand_kit)
    cart_path = os.path.join(site_dir, "cart.html")
    with open(cart_path, "w", encoding="utf-8") as f:
        f.write(cart_html)
    files_written += 1

    # 6. Checkout page
    checkout_html = render_checkout_page(design, brand_kit)
    checkout_path = os.path.join(site_dir, "checkout.html")
    with open(checkout_path, "w", encoding="utf-8") as f:
        f.write(checkout_html)
    files_written += 1

    # 7. Order confirmation page
    order_html = render_order_page(design, brand_kit)
    order_path = os.path.join(site_dir, "order.html")
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


def _lighten(hex_color, amount=0.15):
    """Lighten a hex color by amount (0-1)."""
    h = hex_color.lstrip("#")
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        r = min(255, int(r + (255 - r) * amount))
        g = min(255, int(g + (255 - g) * amount))
        b = min(255, int(b + (255 - b) * amount))
        return f"#{r:02x}{g:02x}{b:02x}"
    except (ValueError, IndexError):
        return hex_color


def _hex_to_hsl(hex_color):
    """Convert hex color to HSL tuple (h, s%, l%)."""
    h = hex_color.lstrip("#")
    try:
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        mx = max(r, g, b)
        mn = min(r, g, b)
        l = (mx + mn) / 2
        if mx == mn:
            return (0, 0, int(l * 100))
        d = mx - mn
        s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
        if mx == r:
            h_val = ((g - b) / d + (6 if g < b else 0)) * 60
        elif mx == g:
            h_val = ((b - r) / d + 2) * 60
        else:
            h_val = ((r - g) / d + 4) * 60
        return (int(h_val), int(s * 100), int(l * 100))
    except (ValueError, IndexError):
        return (0, 0, 50)
