/* Init the Markdown toolbar on Django admin problem-edit textareas.
   Loaded after md-toolbar.js (see ProblemAdmin.Media). */
(function () {
    'use strict';
    function init() {
        var token = document.querySelector('[name=csrfmiddlewaretoken]');
        MdToolbar.csrfToken = token ? token.value : '';
        document.querySelectorAll('textarea.md-editor').forEach(function (ta) {
            if (ta.dataset.mdToolbarInited) return;
            ta.dataset.mdToolbarInited = '1';
            new MdToolbar(ta, { uploadUrl: '/problems/upload-image/' });
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
