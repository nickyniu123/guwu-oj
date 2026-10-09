/**
 * MdToolbar — 轻量级 Markdown + LaTeX 公式编辑工具栏。
 *
 * 无第三方依赖；挂载到任意 <textarea> 上方，提供：
 *   - 通用 Markdown 格式（粗体/斜体/删除线/行内代码/代码块/链接/表格/列表/引用/标题/分割线）
 *   - 图片上传（点击选择 / 拖拽 / 粘贴），上传成功后在光标处插入 ![alt](url)
 *   - LaTeX 行内 $...$ 与块级 $$...$$
 *   - 常用数学符号快捷按钮
 *
 * 用法：
 *   new MdToolbar(textarea, { uploadUrl: '/problems/upload-image/' });
 *
 * 上传接口约定（见 problems.views.upload_problem_image）：
 *   POST multipart/form-data，字段名 image（也兼容 file），返回
 *   {"ok": true, "url": "..."} 或 {"ok": false, "message": "..."}。
 */
(function (global) {
    'use strict';

    // ── 工具栏按钮定义 ───────────────────────────────────────────────────
    // type: 'wrap'   -> 用 before/after 包裹选中文字，无选中则插入占位符
    //       'line'   -> 在每行前加 prefix（整行/块级操作）
    //       'block'  -> 前后空行插入 before ... after
    //       'insert' -> 直接插入文本
    //       'custom' -> 由 handler(ta) 处理
    var GROUPS = [
        {
            label: '文本',
            items: [
                { title: '粗体', icon: '<b>B</b>', type: 'wrap', before: '**', after: '**', ph: '粗体文本' },
                { title: '斜体', icon: '<i>I</i>', type: 'wrap', before: '*', after: '*', ph: '斜体文本' },
                { title: '删除线', icon: '<s>S</s>', type: 'wrap', before: '~~', after: '~~', ph: '删除线' },
                { title: '行内代码', icon: '<code>{ }</code>', type: 'wrap', before: '`', after: '`', ph: 'code' },
                { title: '代码块', icon: '⌨', type: 'block', before: '```\n', after: '\n```', ph: '代码' },
                { title: '链接', icon: '🔗', type: 'custom', handler: insertLink },
                { title: '图片', icon: '🖼', type: 'custom', handler: openImagePicker, needsToolbar: true },
            ],
        },
        {
            label: '结构',
            items: [
                { title: '一级标题', icon: 'H1', type: 'line', prefix: '# ' },
                { title: '二级标题', icon: 'H2', type: 'line', prefix: '## ' },
                { title: '三级标题', icon: 'H3', type: 'line', prefix: '### ' },
                { title: '引用', icon: '❝', type: 'line', prefix: '> ' },
                { title: '无序列表', icon: '•', type: 'line', prefix: '- ' },
                { title: '有序列表', icon: '1.', type: 'line', prefix: '1. ' },
                { title: '表格', icon: '▦', type: 'block', before: '| 列1 | 列2 |\n| --- | --- |\n| 内容 | 内容 |\n', after: '', ph: '' },
                { title: '分割线', icon: '—', type: 'block', before: '\n---\n', after: '', ph: '' },
            ],
        },
        {
            label: '公式',
            items: [
                { title: '行内公式', icon: '∑', type: 'wrap', before: '$', after: '$', ph: '公式' },
                { title: '块级公式', icon: '∫', type: 'block', before: '$$\n', after: '\n$$', ph: '公式' },
            ],
        },
        {
            label: '符号',
            items: [
                { title: '≤', icon: '≤', type: 'insert', text: '\\le ' },
                { title: '≥', icon: '≥', type: 'insert', text: '\\ge ' },
                { title: '≠', icon: '≠', type: 'insert', text: '\\ne ' },
                { title: '≈', icon: '≈', type: 'insert', text: '\\approx ' },
                { title: '±', icon: '±', type: 'insert', text: '\\pm ' },
                { title: '×', icon: '×', type: 'insert', text: '\\times ' },
                { title: '÷', icon: '÷', type: 'insert', text: '\\div ' },
                { title: '√', icon: '√', type: 'insert', text: '\\sqrt{}' },
                { title: '∞', icon: '∞', type: 'insert', text: '\\infty ' },
                { title: '∑', icon: '∑', type: 'insert', text: '\\sum_{i=1}^{n} ' },
                { title: '∫', icon: '∫', type: 'insert', text: '\\int_{a}^{b} ' },
                { title: 'π', icon: 'π', type: 'insert', text: '\\pi ' },
                { title: 'α', icon: 'α', type: 'insert', text: '\\alpha ' },
                { title: 'β', icon: 'β', type: 'insert', text: '\\beta ' },
                { title: 'θ', icon: 'θ', type: 'insert', text: '\\theta ' },
                { title: 'Δ', icon: 'Δ', type: 'insert', text: '\\Delta ' },
                { title: '∈', icon: '∈', type: 'insert', text: '\\in ' },
                { title: '∉', icon: '∉', type: 'insert', text: '\\notin ' },
                { title: '∪', icon: '∪', type: 'insert', text: '\\cup ' },
                { title: '∩', icon: '∩', type: 'insert', text: '\\cap ' },
                { title: '∀', icon: '∀', type: 'insert', text: '\\forall ' },
                { title: '∃', icon: '∃', type: 'insert', text: '\\exists ' },
                { title: '⇒', icon: '⇒', type: 'insert', text: '\\Rightarrow ' },
                { title: '⇔', icon: '⇔', type: 'insert', text: '\\Leftrightarrow ' },
            ],
        },
    ];

    // ── 文本工具 ─────────────────────────────────────────────────────────
    function setSel(ta, start, end) {
        ta.focus();
        ta.setSelectionRange(start, end);
    }

    function insertAtCursor(ta, text, selectLen) {
        var start = ta.selectionStart;
        var end = ta.selectionEnd;
        var before = ta.value.slice(0, start);
        var after = ta.value.slice(end);
        ta.value = before + text + after;
        var caret = start + (selectLen != null ? selectLen : text.length);
        setSel(ta, caret, caret);
        ta.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function wrapSelection(ta, before, after, placeholder) {
        var start = ta.selectionStart;
        var end = ta.selectionEnd;
        var sel = ta.value.slice(start, end);
        if (!sel) {
            sel = placeholder || '';
        }
        var inserted = before + sel + after;
        ta.value = ta.value.slice(0, start) + inserted + ta.value.slice(end);
        var innerStart = start + before.length;
        setSel(ta, innerStart, innerStart + sel.length);
        ta.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function prefixLines(ta, prefix) {
        var start = ta.selectionStart;
        var end = ta.selectionEnd;
        var value = ta.value;
        var lineStart = value.lastIndexOf('\n', start - 1) + 1;
        var lineEnd = value.indexOf('\n', end);
        if (lineEnd < 0) lineEnd = value.length;
        var block = value.slice(lineStart, lineEnd);
        var lines = block.split('\n').map(function (l) {
            return l.startsWith(prefix) ? l.slice(prefix.length) : prefix + l;
        });
        var newBlock = lines.join('\n');
        ta.value = value.slice(0, lineStart) + newBlock + value.slice(lineEnd);
        setSel(ta, lineStart, lineStart + newBlock.length);
        ta.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function insertBlock(ta, before, after, placeholder) {
        var start = ta.selectionStart;
        var end = ta.selectionEnd;
        var value = ta.value;
        // ensure leading blank line unless at BOF
        var padStart = (start > 0 && value[start - 1] !== '\n') ? '\n' : '';
        var padEnd = (end < value.length && value[end] !== '\n') ? '\n' : '';
        var sel = value.slice(start, end) || placeholder || '';
        var inserted = padStart + before + sel + after + padEnd;
        ta.value = value.slice(0, start) + inserted + value.slice(end);
        var innerStart = start + padStart.length + before.length;
        setSel(ta, innerStart, innerStart + sel.length);
        ta.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function applyItem(toolbar, ta, item) {
        switch (item.type) {
            case 'wrap':
                wrapSelection(ta, item.before, item.after, item.ph);
                break;
            case 'line':
                prefixLines(ta, item.prefix);
                break;
            case 'block':
                insertBlock(ta, item.before, item.after, item.ph);
                break;
            case 'insert':
                insertAtCursor(ta, item.text);
                break;
            case 'custom':
                item.handler(ta, toolbar);
                break;
        }
    }

    function insertLink(ta) {
        var url = window.prompt('请输入链接地址：', 'https://');
        if (!url) return;
        var text = ta.value.slice(ta.selectionStart, ta.selectionEnd) || '链接文字';
        insertAtCursor(ta, '[' + text + '](' + url + ')');
    }

    // ── 图片上传 ─────────────────────────────────────────────────────────
    var sharedModal = null;

    function ensureModal() {
        if (sharedModal) return sharedModal;
        var modal = document.createElement('div');
        modal.className = 'md-toolbar-modal';
        modal.innerHTML =
            '<div class="md-toolbar-modal-backdrop"></div>' +
            '<div class="md-toolbar-modal-dialog">' +
            '<div class="md-toolbar-modal-header">' +
            '<h6 class="mb-0">插入图片</h6>' +
            '<button type="button" class="md-toolbar-close" aria-label="关闭">&times;</button>' +
            '</div>' +
            '<div class="md-toolbar-modal-body">' +
            '<div class="md-toolbar-dropzone" id="md-dropzone">' +
            '<i class="bi bi-cloud-upload" style="font-size:2rem;"></i>' +
            '<p>点击选择图片，或拖拽 / 粘贴（Ctrl+V）到此</p>' +
            '<p class="text-muted small">支持 PNG / JPG / GIF / WebP，单张不超过 2 MB</p>' +
            '</div>' +
            '<input type="file" id="md-file-input" accept="image/*" style="display:none">' +
            '<div id="md-upload-status" class="mt-3 small"></div>' +
            '</div></div>';
        document.body.appendChild(modal);
        sharedModal = modal;

        var dz = modal.querySelector('#md-dropzone');
        var input = modal.querySelector('#md-file-input');

        dz.onclick = function () { input.click(); };
        input.onchange = function (e) {
            if (e.target.files && e.target.files.length) {
                handleFiles(e.target.files);
            }
            e.target.value = '';
        };

        ['dragenter', 'dragover'].forEach(function (ev) {
            dz.addEventListener(ev, function (e) {
                e.preventDefault();
                dz.classList.add('drag-over');
            });
        });
        ['dragleave', 'drop'].forEach(function (ev) {
            dz.addEventListener(ev, function (e) {
                e.preventDefault();
                dz.classList.remove('drag-over');
            });
        });
        dz.addEventListener('drop', function (e) {
            if (e.dataTransfer && e.dataTransfer.files) handleFiles(e.dataTransfer.files);
        });

        // 粘贴：只在弹窗可见时拦截图片
        document.addEventListener('paste', function (e) {
            if (modal.style.display !== 'flex') return;
            var items = (e.clipboardData || window.clipboardData).items;
            for (var i = 0; i < items.length; i++) {
                if (items[i].type.indexOf('image') !== -1) {
                    var file = items[i].getAsFile();
                    if (file) { e.preventDefault(); handleFiles([file]); break; }
                }
            }
        });

        modal.querySelector('.md-toolbar-close').addEventListener('click', closeModal);
        modal.querySelector('.md-toolbar-modal-backdrop').addEventListener('click', closeModal);
        return modal;
    }

    function handleFiles(files) {
        if (!sharedModal || !files || !files.length) return;
        uploadImage(files[0], sharedModal._target, sharedModal.querySelector('#md-upload-status'));
    }

    function openImagePicker(ta, toolbar) {
        var modal = ensureModal();
        modal._target = ta;
        modal._uploadUrl = toolbar.uploadUrl || MdToolbar.uploadUrl;
        modal._csrfToken = toolbar.csrfToken || MdToolbar.csrfToken;
        modal.querySelector('#md-upload-status').textContent = '';
        modal.style.display = 'flex';
    }

    function closeModal() {
        if (sharedModal) sharedModal.style.display = 'none';
    }

    function uploadImage(file, ta, statusEl) {
        if (!file.type.startsWith('image/')) {
            statusEl.innerHTML = '<span class="text-danger">请选择图片文件。</span>';
            return;
        }
        if (file.size > 2 * 1024 * 1024) {
            statusEl.innerHTML = '<span class="text-danger">图片不能超过 2 MB。</span>';
            return;
        }
        statusEl.innerHTML = '<span class="text-muted">上传中…</span>';

        var fd = new FormData();
        fd.append('image', file);
        var xhr = new XMLHttpRequest();
        xhr.open('POST', sharedModal._uploadUrl, true);
        if (sharedModal._csrfToken) {
            xhr.setRequestHeader('X-CSRFToken', sharedModal._csrfToken);
        }
        xhr.onload = function () {
            var data;
            try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
            if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) {
                var alt = file.name.replace(/\.[^.]+$/, '').slice(0, 40) || 'image';
                insertAtCursor(ta, '![' + alt + '](' + data.url + ')');
                statusEl.innerHTML = '<span class="text-success">上传成功，已插入图片。</span>';
                setTimeout(closeModal, 600);
            } else {
                var msg = (data && data.message) || ('上传失败（HTTP ' + xhr.status + '）');
                statusEl.innerHTML = '<span class="text-danger">' + msg + '</span>';
            }
        };
        xhr.onerror = function () {
            statusEl.innerHTML = '<span class="text-danger">网络错误，上传失败。</span>';
        };
        xhr.send(fd);
    }

    // ── MdToolbar 主类 ───────────────────────────────────────────────────
    function MdToolbar(ta, opts) {
        opts = opts || {};
        this.ta = ta;
        this.uploadUrl = opts.uploadUrl || MdToolbar.uploadUrl;
        this.csrfToken = opts.csrfToken || MdToolbar.csrfToken;
        this._build();
    }

    MdToolbar.uploadUrl = '/problems/upload-image/';
    MdToolbar.csrfToken = '';

    MdToolbar.prototype._build = function () {
        var wrapper = document.createElement('div');
        wrapper.className = 'md-toolbar-wrapper';

        var bar = document.createElement('div');
        bar.className = 'md-toolbar';

        GROUPS.forEach(function (group) {
            var grp = document.createElement('div');
            grp.className = 'md-toolbar-group';
            grp.title = group.label;
            group.items.forEach(function (item) {
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'md-toolbar-btn';
                btn.title = item.title;
                btn.innerHTML = item.icon;
                btn.addEventListener('click', function (e) {
                    e.preventDefault();
                    applyItem(this, this.ta, item);
                }.bind(this));
                grp.appendChild(btn);
            }, this);
            bar.appendChild(grp);
        }, this);

        var ta = this.ta;
        ta.parentNode.insertBefore(wrapper, ta);
        wrapper.appendChild(bar);
        wrapper.appendChild(ta);
    };

    global.MdToolbar = MdToolbar;
})(window);
