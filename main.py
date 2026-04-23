import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import re
import os
import csv
import json
import urllib.request
import urllib.parse
import urllib.error
from html.parser import HTMLParser
from datetime import datetime

# ── 다크 테마 색상 ──
BG       = "#0d1117"
BG2      = "#161b22"
BG3      = "#21262d"
BORDER   = "#30363d"
FG       = "#c9d1d9"
FG2      = "#8b949e"
FG3      = "#6e7681"
BLUE     = "#388bfd"
GREEN    = "#238636"
GREEN2   = "#3fb950"
ORANGE   = "#f59e0b"
RED      = "#b91c1c"
PURPLE   = "#a78bfa"
WHITE    = "#e6edf3"

# ── 정규식 ──
RE_INVITE   = re.compile(r't\.me/[+]|t\.me/joinchat/')
RE_MEDIA    = re.compile(r'\.(webm|mp4|jpg|jpeg|png|gif|webp|pdf|avi|mov)$', re.I)
RE_PRIV_MSG = re.compile(r'/c/\d+/\d+')
RE_THREAD   = re.compile(r'/[A-Za-z0-9_]{3,}/\d+$')
RE_PLUS     = re.compile(r'/[+]')
RE_TME_HREF = re.compile(r'href=["\']?(https?://t\.me/[^"\'>\s]+)', re.I)
RE_TRAIL_SL = re.compile(r'/+$')
RE_NEWLINES = re.compile(r'[\n\r]+')


def get_link_type(url):
    return 'invite' if RE_INVITE.search(url) else 'public'


def should_skip(url):
    if RE_MEDIA.search(url):    return True
    if RE_PRIV_MSG.search(url): return True
    if RE_THREAD.search(url) and not RE_PLUS.search(url): return True
    return False


def clean_desc(desc):
    if not desc: return ''
    t = desc.strip()
    if re.match(r'^if you have telegram,?\s+you can', t, re.I): return ''
    if re.match(r'^you can view and join', t, re.I): return ''
    if re.match(r'^you can contact', t, re.I): return ''
    return desc.strip()


def _fetch_html(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    try:
        return raw.decode('utf-8')
    except Exception:
        return raw.decode('cp949', errors='replace')


def _parse_html_clean(html, pattern, flags=re.I):
    m = re.search(pattern, html, flags)
    return re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else None


def fetch_info(url):
    """텔레그램 페이지에서 정보 파싱 (기본 + /s/ 미리보기 병행)"""
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8',
    }
    try:
        html = _fetch_html(url, HEADERS)

        # ── 제목 ──
        title = _parse_html_clean(html, r'<div class="tgme_page_title"[^>]*>[\s\S]*?<span[^>]*>([\s\S]*?)</span>')
        if not title:
            title = _parse_html_clean(html, r'property="og:title" content="([^"]+)"')

        # ── 설명 ──
        description = _parse_html_clean(html, r'<div class="tgme_page_description[^>]*>([\s\S]*?)</div>')
        if not description:
            description = _parse_html_clean(html, r'property="og:description" content="([^"]+)"')
        description = clean_desc(description)

        # ── 멤버수 ──
        m_extra = re.search(r'<div class="tgme_page_extra">([\s\S]*?)</div>', html, re.I)
        extra_text = re.sub(r'<[^>]+>', '', m_extra.group(1)).strip() if m_extra else ''
        m_mem = re.search(r'([\d\s,]+)\s*(subscribers?|members?|참여자|구독자)', extra_text + ' ' + html, re.I)
        members = re.sub(r'[\s,]', '', m_mem.group(1)).strip() if m_mem else None

        # ── 프로필 사진 ──
        m_photo = re.search(r'<img class="tgme_page_photo_image"[^>]*src="([^"]+)"', html)
        photo = m_photo.group(1) if m_photo else None

        # ── 타입 판별 ──
        m_action = re.search(r'<a class="tgme_action_button_new[^"]*"[^>]*>([\s\S]*?)</a>', html, re.I)
        action = re.sub(r'<[^>]+>', '', m_action.group(1)).strip() if m_action else None

        link_type = 'unknown'
        if action == 'View Channel' or 'subscribers' in html:  link_type = 'channel'
        elif action == 'Join Group' or 'members' in html:      link_type = 'group'
        elif action == 'Send Message':                          link_type = 'user'
        elif '/+' in url or '/joinchat/' in url:               link_type = 'invite_group'

        # ── username ──
        m_user = re.search(r'<div class="tgme_page_extra">\s*@([a-zA-Z0-9_]+)\s*</div>', html)
        username = m_user.group(1) if m_user else None

        # ── /s/ 미리보기 페이지 추가 파싱 (공개채널만, 최근 게시글/공지) ──
        recent_posts = []
        pinned_post  = None
        preview_url  = None

        is_invite = '/+' in url or '/joinchat/' in url
        if not is_invite and username:
            preview_url = f"https://t.me/s/{username}"
        elif not is_invite and link_type in ('channel', 'group'):
            # URL에서 username 추출 시도
            m_uname = re.search(r't\.me/([A-Za-z0-9_]{3,})', url)
            if m_uname:
                preview_url = f"https://t.me/s/{m_uname.group(1)}"

        if preview_url:
            try:
                s_html = _fetch_html(preview_url, HEADERS)

                # 최근 게시글 (최대 5개)
                raw_msgs = re.findall(
                    r'<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)</div>',
                    s_html
                )
                for raw in raw_msgs[:5]:
                    text = re.sub(r'<[^>]+>', '', raw).strip()
                    text = re.sub(r'\s+', ' ', text)
                    if text and len(text) > 5:
                        recent_posts.append(text[:300])

                # 핀 메시지 (공지)
                m_pin = re.search(
                    r'tgme_widget_message_pinned[\s\S]*?'
                    r'<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)</div>',
                    s_html
                )
                if m_pin:
                    pinned_post = re.sub(r'<[^>]+>', '', m_pin.group(1)).strip()
                    pinned_post = re.sub(r'\s+', ' ', pinned_post)[:300]

                # /s/ 페이지에서 멤버수 보완
                if not members:
                    m_mem2 = re.search(r'([\d\s,]+)\s*(subscribers?|members?)', s_html, re.I)
                    if m_mem2:
                        members = re.sub(r'[\s,]', '', m_mem2.group(1)).strip()

            except Exception:
                pass  # /s/ 실패해도 기본 정보는 있음

        return {
            'status':       'ok',
            'title':        title,
            'description':  description,
            'members':      members,
            'type':         link_type,
            'action':       action,
            'username':     username,
            'photo':        photo,
            'is_invite':    is_invite,
            'recent_posts': recent_posts,   # 최근 게시글 리스트
            'pinned_post':  pinned_post,    # 공지(핀 메시지)
            'preview_url':  preview_url,
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def extract_links_from_html(html_text, source_name):
    links = []
    seen = set()
    for m in RE_TME_HREF.finditer(html_text):
        url = m.group(1)
        url = RE_TRAIL_SL.sub('', url.split('?')[0])
        url = url.replace('&amp;', '&')
        if should_skip(url): continue
        if url not in seen:
            seen.add(url)
            links.append({'url': url, 'type': get_link_type(url), 'source': source_name, 'info': None})
    return links


# ══════════════════════════════════════════════
#  메인 앱
# ══════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("텔레그램 링크 추출기")
        self.geometry("1280x780")
        self.minsize(900, 600)
        self.configure(bg=BG)

        self.all_links = []   # {'url','type','source','info'}
        self.filtered  = []
        self.cur_tab   = 'all'
        self.bulk_stop = False
        self.fetched_count = 0
        self._bulk_thread  = None

        self._build_ui()
        self._apply_filter()

    # ── UI 구성 ──────────────────────────────
    def _build_ui(self):
        # 헤더
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill='x', padx=20, pady=(16, 0))
        tk.Label(hdr, text="✈  텔레그램 링크 추출기",
                 bg=BG, fg=WHITE, font=('맑은 고딕', 15, 'bold')).pack(side='left')
        tk.Label(hdr, text="  HTML 파일에서 그룹·채널 링크 추출 + 정보 조회",
                 bg=BG, fg=FG2, font=('맑은 고딕', 10)).pack(side='left', pady=3)

        # 메인 컨테이너
        main = tk.Frame(self, bg=BG)
        main.pack(fill='both', expand=True, padx=20, pady=12)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        # 좌측 패널
        left = tk.Frame(main, bg=BG, width=290)
        left.grid(row=0, column=0, sticky='ns', padx=(0, 12))
        left.pack_propagate(False)
        self._build_left(left)

        # 우측 패널
        right = tk.Frame(main, bg=BG2, bd=1, relief='solid')
        right.grid(row=0, column=1, sticky='nsew')
        self._build_right(right)

    def _card(self, parent, title=None):
        """다크 카드 프레임"""
        outer = tk.Frame(parent, bg=BG2, bd=1, relief='solid')
        outer.pack(fill='x', pady=(0, 10))
        if title:
            tk.Label(outer, text=title, bg=BG2, fg=FG2,
                     font=('맑은 고딕', 8, 'bold')).pack(anchor='w', padx=12, pady=(10, 4))
        inner = tk.Frame(outer, bg=BG2)
        inner.pack(fill='x', padx=12, pady=(0, 12))
        return inner

    def _btn(self, parent, text, cmd, color=GREEN, fg=WHITE, width=None):
        kw = dict(bg=color, fg=fg, font=('맑은 고딕', 10, 'bold'),
                  relief='flat', cursor='hand2', command=cmd, pady=6)
        if width: kw['width'] = width
        b = tk.Button(parent, text=text, **kw)
        b.pack(fill='x', pady=2)
        return b

    def _build_left(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        inner = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=inner, anchor='nw')

        def _resize(e):
            canvas.itemconfig(win_id, width=e.width)
        canvas.bind('<Configure>', _resize)

        def _scroll(e):
            canvas.yview_scroll(int(-1*(e.delta/120)), 'units')
        canvas.bind_all('<MouseWheel>', _scroll)

        inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))

        # ── 파일 업로드 카드 ──
        c = self._card(inner, "파일 업로드")
        self._btn(c, "📂  HTML 파일 선택", self._open_files, color=BG3, fg=FG)
        self.file_label = tk.Label(c, text="파일 없음", bg=BG2, fg=FG3,
                                   font=('맑은 고딕', 9), wraplength=220, justify='left')
        self.file_label.pack(anchor='w', pady=(4, 0))
        self.parse_btn = self._btn(c, "⚡  링크 추출 시작", self._start_parse)
        self.parse_btn.config(state='disabled')

        # 진행바
        self.prog_var = tk.DoubleVar()
        self.prog_bar = ttk.Progressbar(c, variable=self.prog_var, maximum=100)
        self.prog_bar.pack(fill='x', pady=(6, 0))
        self.prog_label = tk.Label(c, text="", bg=BG2, fg=FG3, font=('맑은 고딕', 8))
        self.prog_label.pack(anchor='w')

        # ── 텍스트 직접 입력 카드 ──
        c2 = self._card(inner, "텍스트로 직접 입력")
        self.text_input = tk.Text(c2, height=7, bg=BG3, fg=FG,
                                  insertbackground=FG, relief='flat',
                                  font=('Consolas', 9), wrap='word', bd=4)
        self.text_input.pack(fill='x')
        self.text_input.insert('1.0', 'https://t.me/+XXXXX\nhttps://t.me/username')
        self.text_input.bind('<FocusIn>', lambda e: self._clear_placeholder())
        self._btn(c2, "➕  링크 목록에 추가", self._add_from_text)

        # ── 통계 카드 ──
        c3 = self._card(inner, "추출 결과")
        stat_f = tk.Frame(c3, bg=BG2)
        stat_f.pack(fill='x')
        self.stat_vars = {}
        for i, (key, label, color) in enumerate([
            ('total','전체',BLUE), ('uniq','중복제거',GREEN2),
            ('invite','초대링크',BLUE), ('public','공개채널',PURPLE)
        ]):
            col = i % 2
            row = i // 2
            box = tk.Frame(stat_f, bg=BG3, bd=1, relief='solid')
            box.grid(row=row, column=col, padx=3, pady=3, sticky='ew')
            stat_f.columnconfigure(col, weight=1)
            v = tk.StringVar(value='0')
            self.stat_vars[key] = v
            tk.Label(box, textvariable=v, bg=BG3, fg=color,
                     font=('맑은 고딕', 18, 'bold')).pack(pady=(8, 0))
            tk.Label(box, text=label, bg=BG3, fg=FG3,
                     font=('맑은 고딕', 8)).pack(pady=(0, 8))

        # ── 일괄 조회 카드 ──
        c4 = self._card(inner, "일괄 정보 조회")
        self.bulk_prog_var = tk.DoubleVar()
        self.bulk_bar = ttk.Progressbar(c4, variable=self.bulk_prog_var, maximum=100)
        self.bulk_bar.pack(fill='x', pady=(0, 4))
        self.bulk_label = tk.Label(c4, text="", bg=BG2, fg=FG3, font=('맑은 고딕', 8))
        self.bulk_label.pack(anchor='w', pady=(0, 4))

        self.bulk_all_btn    = self._btn(c4, "📡  전체 조회 (공개+초대)", lambda: self._bulk_fetch('all'))
        self.bulk_invite_btn = self._btn(c4, "🔗  초대링크만 조회",       lambda: self._bulk_fetch('invite'), color=BG3, fg=FG)
        self.bulk_public_btn = self._btn(c4, "👁  공개채널만 조회",       lambda: self._bulk_fetch('public'), color=BG3, fg=FG)
        self.stop_btn        = self._btn(c4, "⏹  조회 중단",              self._stop_bulk,                   color=RED)
        self.stop_btn.pack_forget()

        self.fetched_label = tk.Label(c4, text="✅ 조회 완료: 0개",
                                      bg=BG2, fg=GREEN2, font=('맑은 고딕', 9))
        self.fetched_label.pack(anchor='w', pady=(4, 0))

        # ── 필터 & 내보내기 카드 ──
        c5 = self._card(inner, "필터 & 내보내기")

        # 검색
        search_f = tk.Frame(c5, bg=BG2)
        search_f.pack(fill='x', pady=(0, 6))
        tk.Label(search_f, text="🔍", bg=BG2, fg=FG2).pack(side='left')
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', lambda *a: self._apply_filter())
        se = tk.Entry(search_f, textvariable=self.search_var,
                      bg=BG3, fg=FG, insertbackground=FG,
                      relief='flat', font=('맑은 고딕', 10), bd=4)
        se.pack(side='left', fill='x', expand=True)

        # 탭 버튼
        tab_f = tk.Frame(c5, bg=BG2)
        tab_f.pack(fill='x', pady=(0, 6))
        self.tab_btns = {}
        for t, label in [('all','전체'), ('invite','초대링크'), ('public','공개채널')]:
            b = tk.Button(tab_f, text=label, bg=BLUE if t=='all' else BG3,
                          fg=WHITE if t=='all' else FG2,
                          relief='flat', cursor='hand2', font=('맑은 고딕', 9),
                          padx=8, pady=3,
                          command=lambda x=t: self._set_tab(x))
            b.pack(side='left', padx=2)
            self.tab_btns[t] = b

        # 중복제거 체크
        self.dedupe_var = tk.BooleanVar(value=True)
        tk.Checkbutton(c5, text="중복 제거", variable=self.dedupe_var,
                       bg=BG2, fg=FG, selectcolor=BG3, activebackground=BG2,
                       command=self._apply_filter).pack(anchor='w', pady=(0, 6))

        self._btn(c5, "📄  TXT 다운로드",  self._export_txt)
        self._btn(c5, "📊  CSV 다운로드",  self._export_csv, color=BG3, fg=FG)
        self._btn(c5, "📋  전체 복사",      self._copy_all,   color=BG3, fg=FG)
        self._btn(c5, "🗑  초기화",         self._clear_all,  color=RED)

        self._files = []

    def _build_right(self, parent):
        # 헤더
        hdr = tk.Frame(parent, bg=BG2)
        hdr.pack(fill='x', padx=16, pady=(12, 8))
        tk.Label(hdr, text="✈ 링크 목록", bg=BG2, fg=WHITE,
                 font=('맑은 고딕', 12, 'bold')).pack(side='left')
        self.badge_var = tk.StringVar(value='0')
        tk.Label(hdr, textvariable=self.badge_var, bg=BLUE, fg=WHITE,
                 font=('맑은 고딕', 9, 'bold'), padx=8, pady=1).pack(side='left', padx=8)
        self.result_sub = tk.Label(hdr, text="", bg=BG2, fg=FG3, font=('맑은 고딕', 9))
        self.result_sub.pack(side='left')

        # 트리뷰
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Treeview',
                        background=BG2, fieldbackground=BG2,
                        foreground=FG, rowheight=28,
                        bordercolor=BORDER, borderwidth=0,
                        font=('맑은 고딕', 9))
        style.configure('Treeview.Heading',
                        background=BG3, foreground=FG2,
                        relief='flat', font=('맑은 고딕', 9, 'bold'))
        style.map('Treeview', background=[('selected', '#1f3558')])

        cols = ('no','badge','url','title','members','type','source')
        self.tree = ttk.Treeview(parent, columns=cols, show='headings', selectmode='extended')

        for col, label, width, anchor in [
            ('no',     '#',      40,  'center'),
            ('badge',  '구분',    60,  'center'),
            ('url',    'URL',    300,  'w'),
            ('title',  '제목',   180,  'w'),
            ('members','멤버수',  80,  'center'),
            ('type',   '타입',    80,  'center'),
            ('source', '출처',   100,  'w'),
        ]:
            self.tree.heading(col, text=label, command=lambda c=col: self._sort_col(c))
            self.tree.column(col, width=width, anchor=anchor, minwidth=30)

        sb_y = ttk.Scrollbar(parent, orient='vertical', command=self.tree.yview)
        sb_x = ttk.Scrollbar(parent, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)

        sb_y.pack(side='right', fill='y')
        sb_x.pack(side='bottom', fill='x')
        self.tree.pack(fill='both', expand=True, padx=(12, 0))

        self.tree.bind('<Double-1>', self._on_double_click)
        self.tree.bind('<Return>',   self._on_return)

        # 우클릭 메뉴
        self.ctx_menu = tk.Menu(self, tearoff=0, bg=BG3, fg=FG,
                                activebackground=BLUE, activeforeground=WHITE)
        self.ctx_menu.add_command(label="🔍 정보 조회",    command=self._fetch_selected)
        self.ctx_menu.add_command(label="📋 URL 복사",     command=self._copy_selected)
        self.ctx_menu.add_command(label="🌐 브라우저 열기", command=self._open_browser)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="🗑 목록에서 삭제", command=self._delete_selected)
        self.tree.bind('<Button-3>', self._show_ctx_menu)

        # 빈 상태 라벨
        self.empty_label = tk.Label(parent,
                                    text="\n\n📂  HTML 파일 업로드 또는\n텍스트 직접 입력으로 링크를 추가하세요",
                                    bg=BG2, fg=FG3, font=('맑은 고딕', 11))
        self.empty_label.place(relx=0.5, rely=0.5, anchor='center')

        # 정보 패널 (하단)
        self.info_frame = tk.Frame(parent, bg=BG3, height=0)
        self.info_frame.pack(fill='x', side='bottom')
        self.info_text = tk.Text(self.info_frame, bg=BG3, fg=FG,
                                 font=('맑은 고딕', 9), height=0,
                                 relief='flat', state='disabled', wrap='word')
        self.info_text.pack(fill='both', expand=True, padx=12, pady=8)

    # ── 파일 처리 ────────────────────────────
    def _open_files(self):
        paths = filedialog.askopenfilenames(
            title="HTML 파일 선택",
            filetypes=[("HTML 파일", "*.html *.htm"), ("모든 파일", "*.*")]
        )
        if not paths: return
        # 중복 제거
        existing = {(f['name'], f['size']) for f in self._files}
        added = 0
        for p in paths:
            name = os.path.basename(p)
            size = os.path.getsize(p)
            if (name, size) not in existing:
                self._files.append({'path': p, 'name': name, 'size': size})
                existing.add((name, size))
                added += 1
        if added:
            names = ', '.join(f['name'] for f in self._files[-3:])
            if len(self._files) > 3: names += f' 외 {len(self._files)-3}개'
            self.file_label.config(text=f"{len(self._files)}개 파일: {names}")
            self.parse_btn.config(state='normal')
            self._toast(f"{added}개 파일 추가됨")
        else:
            self._toast("중복된 파일입니다", error=True)

    def _start_parse(self):
        if not self._files: return
        self.parse_btn.config(state='disabled')
        threading.Thread(target=self._parse_worker, daemon=True).start()

    def _parse_worker(self):
        total = len(self._files)
        added_total = dup_total = 0
        for i, f in enumerate(self._files):
            self.after(0, lambda i=i, f=f: (
                self.prog_var.set(i / total * 100),
                self.prog_label.config(text=f"{i}/{total}  {f['name']}")
            ))
            try:
                with open(f['path'], 'rb') as fh:
                    raw = fh.read()
                try:
                    text = raw.decode('utf-8')
                except Exception:
                    text = raw.decode('cp949', errors='replace')
                links = extract_links_from_html(text, f['name'])
                existing_urls = {l['url'] for l in self.all_links}
                for lnk in links:
                    if lnk['url'] in existing_urls:
                        dup_total += 1
                    else:
                        self.all_links.append(lnk)
                        existing_urls.add(lnk['url'])
                        added_total += 1
            except Exception as e:
                print(f"파일 오류: {f['name']} - {e}")

        def done():
            self.prog_var.set(100)
            self.prog_label.config(text=f"완료! {added_total}개 추가" + (f" (중복 {dup_total}개)" if dup_total else ""))
            self.parse_btn.config(state='normal')
            self._update_stats()
            self._apply_filter()
            self._toast(f"추출 완료! {added_total}개 추가" + (f" (중복 {dup_total}개 제거)" if dup_total else ""))
        self.after(0, done)

    # ── 텍스트 입력 ──────────────────────────
    def _clear_placeholder(self):
        cur = self.text_input.get('1.0', 'end').strip()
        if cur in ('https://t.me/+XXXXX\nhttps://t.me/username', ''):
            self.text_input.delete('1.0', 'end')

    def _add_from_text(self):
        raw = self.text_input.get('1.0', 'end').strip()
        if not raw:
            self._toast("링크를 입력하세요", error=True); return
        lines = RE_NEWLINES.split(raw)
        added = 0
        existing_urls = {l['url'] for l in self.all_links}
        for line in lines:
            l = line.strip()
            if not l or 't.me/' not in l: continue
            if not l.startswith('http'):
                l = 'https://' + re.sub(r'^.*t\.me/', 't.me/', l, flags=re.I)
            l = RE_TRAIL_SL.sub('', l.split('?')[0])
            if should_skip(l) or l in existing_urls: continue
            self.all_links.append({'url': l, 'type': get_link_type(l), 'source': '직접입력', 'info': None})
            existing_urls.add(l)
            added += 1
        if not added:
            self._toast("추가할 유효한 링크가 없습니다", error=True); return
        self.text_input.delete('1.0', 'end')
        self._update_stats()
        self._apply_filter()
        self._toast(f"{added}개 링크 추가됨")

    # ── 일괄 조회 ────────────────────────────
    def _bulk_fetch(self, mode):
        targets = [l for l in self.filtered
                   if not (l['info'] and l['info'].get('status') == 'ok')]
        if mode == 'invite': targets = [l for l in targets if l['type'] == 'invite']
        elif mode == 'public': targets = [l for l in targets if l['type'] == 'public']

        if not targets:
            self._toast("조회할 링크가 없습니다 (이미 완료됐거나 해당 타입 없음)", error=True)
            return

        self.bulk_stop = False
        for b in (self.bulk_all_btn, self.bulk_invite_btn, self.bulk_public_btn):
            b.config(state='disabled')
        self.stop_btn.pack(fill='x', pady=2)

        self._bulk_thread = threading.Thread(
            target=self._bulk_worker, args=(targets,), daemon=True)
        self._bulk_thread.start()

    def _bulk_worker(self, targets):
        total = len(targets)
        done  = 0
        CONC  = 3

        import concurrent.futures

        def fetch_one(link):
            nonlocal done
            if self.bulk_stop: return
            data = fetch_info(link['url'])
            link['info'] = data
            done += 1
            if data.get('status') == 'ok':
                self.fetched_count += 1
            pct = round(done / total * 100)
            self.after(0, lambda: (
                self.bulk_prog_var.set(pct),
                self.bulk_label.config(text=f"{done}/{total}  {link['url'][:50]}"),
                self.fetched_label.config(text=f"✅ 조회 완료: {self.fetched_count}개"),
                self._apply_filter()
            ))

        with concurrent.futures.ThreadPoolExecutor(max_workers=CONC) as ex:
            futures = []
            for link in targets:
                if self.bulk_stop: break
                futures.append(ex.submit(fetch_one, link))
            concurrent.futures.wait(futures)

        def finish():
            for b in (self.bulk_all_btn, self.bulk_invite_btn, self.bulk_public_btn):
                b.config(state='normal')
            self.stop_btn.pack_forget()
            self.bulk_label.config(text="완료!")
            self._apply_filter()
            msg = "조회 중단" if self.bulk_stop else f"일괄 조회 완료! {self.fetched_count}개 조회됨"
            self._toast(msg)
        self.after(0, finish)

    def _stop_bulk(self):
        self.bulk_stop = True
        self.stop_btn.pack_forget()
        for b in (self.bulk_all_btn, self.bulk_invite_btn, self.bulk_public_btn):
            b.config(state='normal')

    # ── 필터 & 렌더 ──────────────────────────
    def _set_tab(self, t):
        self.cur_tab = t
        for k, b in self.tab_btns.items():
            b.config(bg=BLUE if k==t else BG3, fg=WHITE if k==t else FG2)
        self._apply_filter()

    def _apply_filter(self):
        q  = self.search_var.get().strip().lower()
        dd = self.dedupe_var.get()
        res = list(self.all_links)
        if self.cur_tab != 'all':
            res = [l for l in res if l['type'] == self.cur_tab]
        if q:
            res = [l for l in res if
                   q in l['url'].lower() or
                   q in l['source'].lower() or
                   (l['info'] and q in (l['info'].get('title') or '').lower()) or
                   (l['info'] and q in (l['info'].get('description') or '').lower())]
        if dd:
            seen = set(); out = []
            for l in res:
                if l['url'] not in seen:
                    seen.add(l['url']); out.append(l)
            res = out
        self.filtered = res
        self._render_tree()

    def _render_tree(self):
        # 기존 항목 제거
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.badge_var.set(str(len(self.filtered)))
        self.result_sub.config(text=f"총 {len(self.filtered)}개" if self.filtered else "")

        if not self.filtered:
            self.empty_label.place(relx=0.5, rely=0.5, anchor='center')
            return
        self.empty_label.place_forget()

        TYPE_LABEL = {
            'channel': '채널', 'group': '그룹', 'user': '유저',
            'invite_group': '초대그룹', 'private': '비공개', 'unknown': '?'
        }

        for i, l in enumerate(self.filtered):
            info   = l.get('info') or {}
            badge  = '🔗 초대' if l['type'] == 'invite' else '👁 공개'
            title  = info.get('title', '') or ''
            members= info.get('members', '') or ''
            ltype  = TYPE_LABEL.get(info.get('type',''), info.get('type','') or '')
            status = info.get('status', '')
            tag    = 'invite' if l['type'] == 'invite' else ('fetched' if status == 'ok' else ('error' if status == 'error' else 'normal'))
            self.tree.insert('', 'end', iid=str(i),
                             values=(i+1, badge, l['url'], title, members, ltype, l['source']),
                             tags=(tag,))

        self.tree.tag_configure('invite',  foreground='#58a6ff')
        self.tree.tag_configure('fetched', foreground=GREEN2)
        self.tree.tag_configure('error',   foreground='#f87171')
        self.tree.tag_configure('normal',  foreground=FG)

    # ── 트리 이벤트 ──────────────────────────
    def _on_double_click(self, e):
        self._fetch_selected()

    def _on_return(self, e):
        self._fetch_selected()

    def _show_ctx_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
            self.ctx_menu.post(e.x_root, e.y_root)

    def _get_selected_links(self):
        sels = self.tree.selection()
        return [self.filtered[int(s)] for s in sels if int(s) < len(self.filtered)]

    def _fetch_selected(self):
        links = self._get_selected_links()
        if not links: return
        # 정보 패널 표시
        link = links[0]
        if link.get('info') and link['info'].get('status') == 'ok':
            self._show_info_panel(link['info'], link['url'])
        else:
            self._show_info_panel(None, link['url'], loading=True)
            threading.Thread(target=self._fetch_single_worker,
                             args=(link,), daemon=True).start()

    def _fetch_single_worker(self, link):
        data = fetch_info(link['url'])
        link['info'] = data
        if data.get('status') == 'ok':
            self.fetched_count += 1
            self.after(0, lambda: self.fetched_label.config(
                text=f"✅ 조회 완료: {self.fetched_count}개"))
        self.after(0, lambda: (
            self._show_info_panel(data, link['url']),
            self._apply_filter()
        ))

    def _show_info_panel(self, info, url, loading=False):
        self.info_text.config(state='normal', height=10)
        self.info_text.delete('1.0', 'end')
        if loading:
            self.info_text.insert('end', f"⏳ 조회 중... {url}")
        elif not info or info.get('status') == 'error':
            err = info.get('error', '비공개 그룹이거나 조회 불가') if info else '정보 없음'
            self.info_text.insert('end', f"❌ 조회 실패: {err}\n🔗 {url}")
        else:
            TYPE_LABEL = {
                'channel':'채널','group':'그룹','user':'유저',
                'invite_group':'초대그룹','private':'비공개','unknown':'?'
            }
            lines = []
            lines.append(f"📌 제목    : {info.get('title') or '(없음)'}")
            lines.append(f"🏷  타입    : {TYPE_LABEL.get(info.get('type',''), info.get('type','') or '알 수 없음')}")
            if info.get('username'): lines.append(f"👤 유저명  : @{info['username']}")
            if info.get('members'):  lines.append(f"👥 멤버수  : {info['members']}명")
            lines.append(f"📝 설명    : {info.get('description') or '(없음)'}")
            # 공지 (핀 메시지)
            if info.get('pinned_post'):
                lines.append(f"📢 공지    : {info['pinned_post'][:300]}")
            # 최근 게시글
            if info.get('recent_posts'):
                lines.append(f"📰 최근글  :")
                for i, post in enumerate(info['recent_posts'][:5], 1):
                    lines.append(f"   [{i}] {post[:200]}")
            lines.append(f"🔗 URL     : {url}")
            self.info_text.insert('end', '\n'.join(lines))
        self.info_text.config(state='disabled')

    def _copy_selected(self):
        links = self._get_selected_links()
        if not links: return
        self.clipboard_clear()
        self.clipboard_append('\n'.join(l['url'] for l in links))
        self._toast(f"{len(links)}개 복사됨!")

    def _open_browser(self):
        import webbrowser
        for l in self._get_selected_links():
            webbrowser.open(l['url'])

    def _delete_selected(self):
        links = self._get_selected_links()
        urls  = {l['url'] for l in links}
        self.all_links = [l for l in self.all_links if l['url'] not in urls]
        self._update_stats()
        self._apply_filter()
        self._toast(f"{len(links)}개 삭제됨")

    def _sort_col(self, col):
        pass  # 필요시 구현

    # ── 내보내기 ─────────────────────────────
    def _export_txt(self):
        if not self.filtered:
            self._toast("내보낼 링크가 없습니다", error=True); return
        path = filedialog.asksaveasfilename(
            defaultextension='.txt',
            initialfile=f"telegram_links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            filetypes=[("텍스트 파일", "*.txt")])
        if not path: return
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(l['url'] for l in self.filtered))
        self._toast(f"{len(self.filtered)}개 TXT 저장!")

    def _export_csv(self):
        if not self.filtered:
            self._toast("내보낼 링크가 없습니다", error=True); return
        path = filedialog.asksaveasfilename(
            defaultextension='.csv',
            initialfile=f"telegram_links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            filetypes=[("CSV 파일", "*.csv")])
        if not path: return
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['번호','URL','링크타입','제목','설명','공지','최근게시글','멤버수','채널타입','유저명','출처파일'])
            for i, l in enumerate(self.filtered):
                info = l.get('info') or {}
                recent = ' | '.join(info.get('recent_posts') or [])
                w.writerow([
                    i+1, l['url'], l['type'],
                    info.get('title',''),
                    info.get('description',''),
                    info.get('pinned_post',''),
                    recent,
                    info.get('members',''),
                    info.get('type',''),
                    info.get('username',''),
                    l['source'],
                ])
        self._toast(f"{len(self.filtered)}개 CSV 저장!")

    def _copy_all(self):
        if not self.filtered:
            self._toast("복사할 링크가 없습니다", error=True); return
        self.clipboard_clear()
        self.clipboard_append('\n'.join(l['url'] for l in self.filtered))
        self._toast(f"{len(self.filtered)}개 복사됨!")

    def _clear_all(self):
        if not messagebox.askyesno("초기화", "모든 링크를 삭제하시겠습니까?"):
            return
        self.all_links = []
        self.filtered  = []
        self._files    = []
        self.fetched_count = 0
        self.file_label.config(text="파일 없음")
        self.parse_btn.config(state='disabled')
        self.prog_var.set(0)
        self.prog_label.config(text="")
        self.bulk_prog_var.set(0)
        self.bulk_label.config(text="")
        self.fetched_label.config(text="✅ 조회 완료: 0개")
        self.search_var.set('')
        self.info_text.config(state='normal', height=0)
        self.info_text.delete('1.0','end')
        self.info_text.config(state='disabled')
        self._update_stats()
        self._apply_filter()
        self._toast("초기화 완료")

    # ── 통계 ─────────────────────────────────
    def _update_stats(self):
        seen = set(); uniq = []
        for l in self.all_links:
            if l['url'] not in seen:
                seen.add(l['url']); uniq.append(l)
        self.stat_vars['total'].set(str(len(self.all_links)))
        self.stat_vars['uniq'].set(str(len(uniq)))
        self.stat_vars['invite'].set(str(sum(1 for l in uniq if l['type']=='invite')))
        self.stat_vars['public'].set(str(sum(1 for l in uniq if l['type']=='public')))

    # ── 토스트 알림 ──────────────────────────
    def _toast(self, msg, error=False):
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes('-topmost', True)
        color = RED if error else GREEN
        tk.Label(win, text=msg, bg=color, fg=WHITE,
                 font=('맑은 고딕', 10), padx=16, pady=10).pack()
        # 우하단 위치
        self.update_idletasks()
        x = self.winfo_x() + self.winfo_width()  - 20
        y = self.winfo_y() + self.winfo_height() - 20
        win.geometry(f"+{x-300}+{y-60}")
        win.after(2500, win.destroy)


if __name__ == '__main__':
    app = App()
    app.mainloop()
