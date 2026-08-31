/**
 * Progressive enhancement for the print form.
 * Without this file the form still works as a plain multipart POST.
 */
(function () {
    "use strict";

    var form = document.getElementById("print-form");
    if (!form) {
        return;
    }

    var MAX_BYTES = 50 * 1024 * 1024;
    var PDF_TYPES = { pdf: true };
    var IMAGE_TYPES = { jpg: true, jpeg: true, png: true };

    var modePdf = document.getElementById("mode-pdf");
    var modeImage = document.getElementById("mode-image");
    var pdfInput = document.getElementById("pdf");
    var imageInput = document.getElementById("image");
    var dropZone = document.getElementById("drop-zone");
    var dropIdle = document.getElementById("drop-idle");
    var dropHint = document.getElementById("drop-hint");
    var preview = document.getElementById("preview");
    var thumb = document.getElementById("thumb");
    var filenameEl = document.getElementById("filename");
    var filesizeEl = document.getElementById("filesize");
    var removeBtn = document.getElementById("remove");
    var browseBtn = document.getElementById("browse");
    var pagesField = document.getElementById("pages-field");
    var pagesInput = document.getElementById("pages");
    var pagesError = document.getElementById("pages-error");
    var copiesInput = document.getElementById("copies");
    var submitBtn = document.getElementById("submit");
    var progress = document.getElementById("progress");
    var bar = document.getElementById("bar");
    var liveMessage = document.getElementById("live-message");
    var pdfPreview = document.getElementById("pdf-preview");
    var previewSummary = document.getElementById("preview-summary");
    var pagesStrip = document.getElementById("pages-strip");
    var toggleAllBtn = document.getElementById("toggle-all");
    var objectUrl = null;

    var pageCount = 0;
    var selectedPages = null;   // Set of 1-based page numbers, or null.
    var pdfLibPromise = null;
    var renderToken = 0;        // Invalidates renders for superseded files.

    function extensionOf(name) {
        var dot = name.lastIndexOf(".");
        return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
    }

    function formatSize(bytes) {
        if (bytes < 1024) {
            return bytes + " B";
        }
        if (bytes < 1024 * 1024) {
            return (bytes / 1024).toFixed(0) + " KB";
        }
        return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    function showMessage(text, ok) {
        liveMessage.textContent = text;
        liveMessage.className = "message " + (ok ? "ok" : "error");
        liveMessage.hidden = !text;
        if (text) {
            liveMessage.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
    }

    function isPdfMode() {
        return modePdf.checked;
    }

    // --- Page selection ---------------------------------------------------

    /** Expand a page expression into a Set. Empty expression means all. */
    function parsePageExpression(expression, total) {
        var pages = new Set();
        var value = (expression || "").trim().replace(/\s+/g, "");

        if (!value) {
            for (var all = 1; all <= total; all++) {
                pages.add(all);
            }
            return pages;
        }

        value.split(",").forEach(function (part) {
            var bounds = part.split("-");
            var start = parseInt(bounds[0], 10);
            var end = bounds.length > 1 ? parseInt(bounds[1], 10) : start;
            for (var page = start; page <= end; page++) {
                if (page >= 1 && page <= total) {
                    pages.add(page);
                }
            }
        });

        return pages;
    }

    /** Collapse a Set back into the shortest expression, e.g. 1-3,7. */
    function serializePages(pages, total) {
        if (pages.size === total) {
            return "";
        }

        var sorted = Array.from(pages).sort(function (a, b) {
            return a - b;
        });

        var parts = [];
        var index = 0;
        while (index < sorted.length) {
            var start = sorted[index];
            var end = start;
            while (
                index + 1 < sorted.length &&
                sorted[index + 1] === end + 1
            ) {
                index += 1;
                end = sorted[index];
            }
            parts.push(start === end ? String(start) : start + "-" + end);
            index += 1;
        }

        return parts.join(",");
    }

    function updateSummary() {
        if (!pageCount) {
            return;
        }

        var chosen = selectedPages ? selectedPages.size : 0;
        var label;

        if (chosen === 0) {
            label = "No pages selected";
        } else if (chosen === pageCount) {
            label =
                pageCount === 1
                    ? "Printing 1 page"
                    : "Printing all " + pageCount + " pages";
        } else {
            label = "Printing " + chosen + " of " + pageCount + " pages";
        }

        previewSummary.textContent = label;
        previewSummary.classList.toggle("none", chosen === 0);

        toggleAllBtn.hidden = false;
        toggleAllBtn.textContent =
            chosen === pageCount ? "Select none" : "Select all";
    }

    function paintTiles() {
        var tiles = pagesStrip.querySelectorAll(".page-tile");
        for (var i = 0; i < tiles.length; i++) {
            var page = parseInt(tiles[i].dataset.page, 10);
            var on = selectedPages.has(page);
            tiles[i].classList.toggle("off", !on);
            tiles[i].setAttribute("aria-pressed", on ? "true" : "false");
        }
        updateSummary();
    }

    /** Write the current selection back into the Pages field. */
    function syncInputFromSelection() {
        pagesInput.value = serializePages(selectedPages, pageCount);

        // An empty expression means "all pages" to CUPS, so an empty
        // selection has to be called out rather than silently inverted.
        var empty = selectedPages.size === 0;
        pagesError.textContent = empty
            ? "Select at least one page to print."
            : "";
        pagesError.hidden = !empty;
    }

    /** Re-read the Pages field into the selection after manual editing. */
    function syncSelectionFromInput() {
        if (!pageCount) {
            return;
        }
        selectedPages = parsePageExpression(pagesInput.value, pageCount);
        paintTiles();
    }

    function resetPreview() {
        renderToken += 1;
        pageCount = 0;
        selectedPages = null;
        pagesStrip.innerHTML = "";
        pdfPreview.hidden = true;
        toggleAllBtn.hidden = true;
        previewSummary.classList.remove("none");
    }

    // --- PDF thumbnails ---------------------------------------------------

    /** Load pdf.js on demand so the initial page stays light on mobile. */
    function loadPdfLib() {
        if (pdfLibPromise) {
            return pdfLibPromise;
        }

        pdfLibPromise = new Promise(function (resolve, reject) {
            var paths = window.LOCALPRINT;
            if (!paths || !paths.pdfLib) {
                reject(new Error("pdf.js path is not configured"));
                return;
            }

            var script = document.createElement("script");
            script.src = paths.pdfLib;
            script.onload = function () {
                if (!window.pdfjsLib) {
                    reject(new Error("pdf.js did not initialise"));
                    return;
                }
                window.pdfjsLib.GlobalWorkerOptions.workerSrc =
                    paths.pdfWorker;
                resolve(window.pdfjsLib);
            };
            script.onerror = function () {
                pdfLibPromise = null;
                reject(new Error("pdf.js could not be loaded"));
            };
            document.head.appendChild(script);
        });

        return pdfLibPromise;
    }

    function readAsArrayBuffer(file) {
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () {
                resolve(reader.result);
            };
            reader.onerror = function () {
                reject(new Error("Could not read the file"));
            };
            reader.readAsArrayBuffer(file);
        });
    }

    function createTile(pageNumber) {
        var tile = document.createElement("button");
        tile.type = "button";
        tile.className = "page-tile";
        tile.dataset.page = String(pageNumber);
        tile.setAttribute("aria-pressed", "true");
        tile.setAttribute("aria-label", "Page " + pageNumber);

        var sheet = document.createElement("span");
        sheet.className = "sheet";
        tile.appendChild(sheet);

        var badge = document.createElement("span");
        badge.className = "page-num";
        badge.textContent = String(pageNumber);
        tile.appendChild(badge);

        return tile;
    }

    /** Draw one page into its tile, replacing the placeholder sheet. */
    function renderPage(pdf, tile, pageNumber, token) {
        return pdf.getPage(pageNumber).then(function (page) {
            if (token !== renderToken) {
                return;
            }

            var width = 120; // Rendered at 2x tile width for crisp output.
            var base = page.getViewport({ scale: 1 });
            var viewport = page.getViewport({ scale: width / base.width });

            var canvas = document.createElement("canvas");
            canvas.width = Math.round(viewport.width);
            canvas.height = Math.round(viewport.height);

            return page.render({
                canvasContext: canvas.getContext("2d"),
                viewport: viewport
            }).promise.then(function () {
                if (token !== renderToken) {
                    return;
                }
                var sheet = tile.querySelector(".sheet");
                if (sheet) {
                    tile.replaceChild(canvas, sheet);
                }
            });
        });
    }

    function buildPreview(file) {
        resetPreview();
        var token = renderToken;

        pdfPreview.hidden = false;
        previewSummary.textContent = "Reading PDF\u2026";

        loadPdfLib()
            .then(function (pdfjsLib) {
                return readAsArrayBuffer(file).then(function (buffer) {
                    return pdfjsLib.getDocument({ data: buffer }).promise;
                });
            })
            .then(function (pdf) {
                if (token !== renderToken) {
                    return;
                }

                pageCount = pdf.numPages;
                selectedPages = parsePageExpression(
                    pagesInput.value,
                    pageCount
                );

                var tiles = [];
                for (var page = 1; page <= pageCount; page++) {
                    var tile = createTile(page);
                    pagesStrip.appendChild(tile);
                    tiles.push(tile);
                }
                paintTiles();

                // Render only what scrolls into view, so a 200 page PDF
                // does not block the phone on selection.
                if (typeof IntersectionObserver === "undefined") {
                    tiles.slice(0, 12).forEach(function (tile, index) {
                        renderPage(pdf, tile, index + 1, token);
                    });
                    return;
                }

                var observer = new IntersectionObserver(
                    function (entries) {
                        entries.forEach(function (entry) {
                            if (!entry.isIntersecting) {
                                return;
                            }
                            observer.unobserve(entry.target);
                            renderPage(
                                pdf,
                                entry.target,
                                parseInt(entry.target.dataset.page, 10),
                                token
                            );
                        });
                    },
                    { root: pagesStrip, rootMargin: "200px" }
                );

                tiles.forEach(function (tile) {
                    observer.observe(tile);
                });
            })
            .catch(function (error) {
                if (token !== renderToken) {
                    return;
                }
                // The preview is a convenience; printing must still work.
                resetPreview();
                showMessage(
                    "Page preview unavailable (" + error.message +
                        "). You can still type a page range and print.",
                    false
                );
            });
    }

    function activeInput() {
        return isPdfMode() ? pdfInput : imageInput;
    }

    function releaseThumb() {
        if (objectUrl) {
            URL.revokeObjectURL(objectUrl);
            objectUrl = null;
        }
    }

    function clearFile() {
        releaseThumb();
        pdfInput.value = "";
        imageInput.value = "";
        thumb.innerHTML = "";
        preview.hidden = true;
        dropIdle.hidden = false;
        dropZone.classList.remove("has-file");
        resetPreview();
    }

    function renderPreview(file) {
        releaseThumb();
        filenameEl.textContent = file.name;
        filesizeEl.textContent = formatSize(file.size);

        if (file.type.indexOf("image/") === 0) {
            objectUrl = URL.createObjectURL(file);
            var img = new Image();
            img.src = objectUrl;
            img.alt = "";
            thumb.innerHTML = "";
            thumb.appendChild(img);
        } else {
            thumb.textContent = "PDF";
            buildPreview(file);
        }

        preview.hidden = false;
        dropIdle.hidden = true;
        dropZone.classList.add("has-file");
    }

    /** Sync which input is active and whether the pages field applies. */
    function syncMode() {
        var pdf = isPdfMode();
        pdfInput.disabled = !pdf;
        imageInput.disabled = pdf;
        pagesField.hidden = !pdf;
        dropHint.textContent = pdf
            ? "PDF \u00b7 up to 50 MB"
            : "JPEG or PNG \u00b7 up to 50 MB";
    }

    /** Assign a dropped/selected file to the input matching its type. */
    function acceptFile(file) {
        if (!file) {
            return;
        }

        var ext = extensionOf(file.name);
        var target;

        if (PDF_TYPES[ext]) {
            modePdf.checked = true;
            target = pdfInput;
        } else if (IMAGE_TYPES[ext]) {
            modeImage.checked = true;
            target = imageInput;
        } else {
            showMessage(
                "Unsupported file type \u201c." + ext + "\u201d. Choose a PDF, JPEG or PNG.",
                false
            );
            return;
        }

        if (file.size > MAX_BYTES) {
            showMessage(
                "\u201c" + file.name + "\u201d is " + formatSize(file.size) +
                    ". The limit is 50 MB.",
                false
            );
            return;
        }

        syncMode();

        // DataTransfer lets a dropped file populate a real file input, so the
        // no-JS submit path and the fetch path stay identical.
        try {
            var transfer = new DataTransfer();
            transfer.items.add(file);
            target.files = transfer.files;
        } catch (err) {
            showMessage("This browser cannot accept dropped files.", false);
            return;
        }

        showMessage("", true);
        renderPreview(file);
    }

    function validatePages() {
        var raw = (pagesInput.value || "").trim();
        if (!raw) {
            pagesError.hidden = true;
            return true;
        }

        // "1 3" must not silently collapse into page 13.
        var spaced = /\d\s+\d/.test(raw);
        var value = raw.replace(/\s+/g, "");
        var valid = !spaced && /^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$/.test(value);

        if (valid) {
            var parts = value.split(",");
            for (var i = 0; i < parts.length; i++) {
                var range = parts[i].split("-");
                var start = parseInt(range[0], 10);
                var end = range.length > 1 ? parseInt(range[1], 10) : start;
                if (start < 1 || end < start) {
                    valid = false;
                    break;
                }
            }
        }

        pagesError.textContent = valid
            ? ""
            : spaced
                ? "Separate page numbers with commas, e.g. 1,3 or 1-3."
                : "Use page numbers like 1, 1-3 or 1,3,5-7.";
        pagesError.hidden = valid;
        return valid;
    }

    function stepCopies(delta) {
        var min = parseInt(copiesInput.min, 10) || 1;
        var max = parseInt(copiesInput.max, 10) || 20;
        var next = (parseInt(copiesInput.value, 10) || min) + delta;
        copiesInput.value = Math.min(max, Math.max(min, next));
    }

    function setBusy(busy) {
        submitBtn.disabled = busy;
        progress.hidden = !busy;
        if (!busy) {
            bar.style.width = "0";
        }
    }

    // --- Wiring -----------------------------------------------------------

    document.documentElement.classList.remove("no-js");

    // The enhanced inputs are visually hidden, which browsers refuse to
    // focus for constraint validation. JS validates before submitting.
    pdfInput.required = false;
    imageInput.required = false;

    modePdf.addEventListener("change", function () {
        syncMode();
        clearFile();
    });
    modeImage.addEventListener("change", function () {
        syncMode();
        clearFile();
    });

    browseBtn.addEventListener("click", function () {
        activeInput().click();
    });

    dropZone.addEventListener("click", function (event) {
        if (event.target.closest("button") || !preview.hidden) {
            return;
        }
        activeInput().click();
    });

    pdfInput.addEventListener("change", function () {
        if (pdfInput.files[0]) {
            acceptFile(pdfInput.files[0]);
        }
    });
    imageInput.addEventListener("change", function () {
        if (imageInput.files[0]) {
            acceptFile(imageInput.files[0]);
        }
    });

    removeBtn.addEventListener("click", function (event) {
        event.stopPropagation();
        clearFile();
        showMessage("", true);
    });

    ["dragenter", "dragover"].forEach(function (name) {
        dropZone.addEventListener(name, function (event) {
            event.preventDefault();
            dropZone.classList.add("dragover");
        });
    });

    ["dragleave", "drop"].forEach(function (name) {
        dropZone.addEventListener(name, function (event) {
            event.preventDefault();
            dropZone.classList.remove("dragover");
        });
    });

    dropZone.addEventListener("drop", function (event) {
        var files = event.dataTransfer && event.dataTransfer.files;
        if (files && files.length) {
            acceptFile(files[0]);
        }
    });

    // Dropping anywhere on the page should not navigate away from the app.
    ["dragover", "drop"].forEach(function (name) {
        window.addEventListener(name, function (event) {
            if (!dropZone.contains(event.target)) {
                event.preventDefault();
            }
        });
    });

    document.getElementById("copies-down").addEventListener("click", function () {
        stepCopies(-1);
    });
    document.getElementById("copies-up").addEventListener("click", function () {
        stepCopies(1);
    });

    pagesInput.addEventListener("input", function () {
        if (validatePages()) {
            syncSelectionFromInput();
        }
    });

    pagesStrip.addEventListener("click", function (event) {
        var tile = event.target.closest(".page-tile");
        if (!tile || !selectedPages) {
            return;
        }

        var page = parseInt(tile.dataset.page, 10);
        if (selectedPages.has(page)) {
            selectedPages.delete(page);
        } else {
            selectedPages.add(page);
        }

        syncInputFromSelection();
        paintTiles();
    });

    toggleAllBtn.addEventListener("click", function () {
        if (!pageCount) {
            return;
        }

        if (selectedPages.size === pageCount) {
            selectedPages = new Set();
        } else {
            selectedPages = parsePageExpression("", pageCount);
        }

        syncInputFromSelection();
        paintTiles();
    });

    form.addEventListener("submit", function (event) {
        var input = activeInput();

        if (!input.files || !input.files.length) {
            event.preventDefault();
            showMessage(
                isPdfMode() ? "Please choose a PDF file." : "Please choose an image.",
                false
            );
            return;
        }

        if (isPdfMode() && !validatePages()) {
            event.preventDefault();
            showMessage("Fix the page selection before printing.", false);
            return;
        }

        if (isPdfMode() && pageCount && selectedPages.size === 0) {
            event.preventDefault();
            showMessage(
                "No pages are selected. Choose at least one page to print.",
                false
            );
            return;
        }

        if (typeof XMLHttpRequest === "undefined" || typeof FormData === "undefined") {
            return; // Fall back to a normal form submission.
        }

        event.preventDefault();
        setBusy(true);
        showMessage("", true);

        var request = new XMLHttpRequest();
        request.open("POST", form.action || window.location.pathname);
        request.setRequestHeader("X-Requested-With", "LocalPrint");

        request.upload.addEventListener("progress", function (progressEvent) {
            if (progressEvent.lengthComputable) {
                var percent = (progressEvent.loaded / progressEvent.total) * 100;
                bar.style.width = percent.toFixed(1) + "%";
            }
        });

        request.addEventListener("load", function () {
            setBusy(false);
            var payload = null;
            try {
                payload = JSON.parse(request.responseText);
            } catch (err) {
                payload = null;
            }

            if (payload && payload.message) {
                showMessage(payload.message, payload.ok === true);
            } else if (request.status >= 200 && request.status < 400) {
                showMessage("Print job submitted.", true);
            } else {
                showMessage("Print failed (HTTP " + request.status + ").", false);
            }

            if (payload && payload.ok) {
                clearFile();
            }
        });

        request.addEventListener("error", function () {
            setBusy(false);
            showMessage("Could not reach the print server.", false);
        });

        request.send(new FormData(form));
    });

    syncMode();
    clearFile();
}());
