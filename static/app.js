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

    // The limit is enforced by the server; mirroring it here only avoids a
    // pointless upload, so it must come from the same configuration.
    var MAX_UPLOAD_MB = (window.LOCALPRINT && window.LOCALPRINT.maxUploadMb) || 0;
    var MAX_BYTES = MAX_UPLOAD_MB * 1024 * 1024;
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
    var queuePanel = document.getElementById("queue-panel");
    var queueState = document.getElementById("queue-state");
    var queueReason = document.getElementById("queue-reason");
    var clearQueueBtn = document.getElementById("clear-queue");
    var printerDot = document.getElementById("printer-dot");
    var pdfPreview = document.getElementById("pdf-preview");
    var previewSummary = document.getElementById("preview-summary");
    var pagesStrip = document.getElementById("pages-strip");
    var toggleAllBtn = document.getElementById("toggle-all");
    var upscaleBox = document.getElementById("upscale");
    var upscaleNote = document.getElementById("upscale-note");
    var upscaleBtn = document.getElementById("upscale-btn");
    var generateBox = document.getElementById("generate");
    var generateToggle = document.getElementById("generate-toggle");
    var generatePanel = document.getElementById("generate-panel");
    var promptInput = document.getElementById("prompt");
    var promptError = document.getElementById("prompt-error");
    var styleSelect = document.getElementById("generate-style");
    var generateBtn = document.getElementById("generate-btn");
    var imageView = document.getElementById("image-view");
    var imageViewImg = document.getElementById("image-view-img");
    var imageViewCaption = document.getElementById("image-view-caption");
    var objectUrl = null;

    var pageCount = 0;
    var selectedPages = null;   // Set of 1-based page numbers, or null.
    var pdfLibPromise = null;
    var renderToken = 0;        // Invalidates renders for superseded files.

    var upscaleToken = 0;       // Invalidates upscale work for old files.
    var upscaleRequest = null;
    var upscaling = false;
    var shownSize = null;       // Displayed size of the current image.

    var generateToken = 0;      // Invalidates a generation the user left.
    var generateRequest = null;
    var generating = false;
    var generateLabel = "Generate";

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

    function showMessage(text, ok, keepScroll) {
        liveMessage.textContent = text;
        liveMessage.className = "message " + (ok ? "ok" : "error");
        liveMessage.hidden = !text;
        if (text && !keepScroll) {
            liveMessage.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
    }

    function isPdfMode() {
        return modePdf.checked;
    }

    // --- Queue status -----------------------------------------------------

    /** The same sentence the server renders, kept in step with it. */
    function describeQueue(status) {
        var jobs = status.jobs || 0;
        var noun = jobs === 1 ? " job" : " jobs";

        if (status.enabled === false) {
            return jobs
                ? "Printer stopped \u00b7 " + jobs + noun + " stuck"
                : "Printer stopped";
        }
        return jobs + noun + " waiting";
    }

    function renderQueue(status) {
        // An unreadable queue says nothing useful, so leave the panel on
        // whatever the page last knew for certain.
        if (!queuePanel || !status || status.known === false) {
            return;
        }

        var stopped = status.enabled === false;

        queueState.textContent = describeQueue(status);
        queueReason.textContent = status.reason || "";
        queueReason.hidden = !status.reason;
        queuePanel.classList.toggle("queue-stopped", stopped);
        queuePanel.hidden = !stopped && !(status.jobs || 0);

        if (printerDot) {
            printerDot.classList.toggle("dot-stopped", stopped);
        }
    }

    function refreshQueue() {
        var url = window.LOCALPRINT && window.LOCALPRINT.queueUrl;
        if (!queuePanel || !url || typeof XMLHttpRequest === "undefined") {
            return;
        }

        var request = new XMLHttpRequest();
        request.open("GET", url);
        request.setRequestHeader("X-Requested-With", "LocalPrint");
        request.addEventListener("load", function () {
            try {
                renderQueue(JSON.parse(request.responseText));
            } catch (err) {
                /* Keep the last known state rather than inventing one. */
            }
        });
        request.send();
    }

    if (queuePanel) {
        queuePanel.addEventListener("submit", function (event) {
            if (typeof XMLHttpRequest === "undefined") {
                return; // Fall back to a normal form submission.
            }

            event.preventDefault();
            if (!window.confirm(
                "Cancel every waiting job and restart the printer?"
            )) {
                return;
            }

            clearQueueBtn.disabled = true;

            var request = new XMLHttpRequest();
            request.open("POST", queuePanel.action);
            request.setRequestHeader("X-Requested-With", "LocalPrint");

            request.addEventListener("load", function () {
                clearQueueBtn.disabled = false;

                var payload = null;
                try {
                    payload = JSON.parse(request.responseText);
                } catch (err) {
                    payload = null;
                }

                if (payload && payload.message) {
                    showMessage(payload.message, payload.ok === true);
                } else {
                    showMessage(
                        "Could not clear the queue (HTTP "
                            + request.status + ").",
                        false
                    );
                }

                if (payload && payload.queue) {
                    renderQueue(payload.queue);
                } else {
                    refreshQueue();
                }
            });

            request.addEventListener("error", function () {
                clearQueueBtn.disabled = false;
                showMessage("Could not reach the print server.", false);
            });

            request.send();
        });
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
        resetUpscale();
        pdfInput.value = "";
        imageInput.value = "";
        thumb.innerHTML = "";
        preview.hidden = true;
        imageView.hidden = true;
        imageViewImg.removeAttribute("src");
        imageViewCaption.textContent = "";
        dropIdle.hidden = false;
        dropZone.classList.remove("has-file");
        resetPreview();
    }

    function renderPreview(file) {
        releaseThumb();
        resetUpscale();
        filenameEl.textContent = file.name;
        filesizeEl.textContent = formatSize(file.size);

        if (file.type.indexOf("image/") === 0) {
            objectUrl = URL.createObjectURL(file);
            var img = new Image();
            var token = upscaleToken;
            img.onload = function () {
                // naturalWidth honours EXIF rotation, so this is the size
                // as it will be printed.
                if (token === upscaleToken) {
                    imageViewCaption.textContent =
                        formatPixels(img.naturalWidth, img.naturalHeight) +
                        " px";
                    checkUpscale(img.naturalWidth, img.naturalHeight);
                }
            };
            img.src = objectUrl;
            img.alt = "";
            thumb.innerHTML = "";
            thumb.appendChild(img);

            // The thumbnail is too small to judge a picture by, so show it
            // properly before it is printed.
            imageViewImg.src = objectUrl;
            imageViewCaption.textContent = "";
            imageView.hidden = false;
        } else {
            thumb.textContent = "PDF";
            imageView.hidden = true;
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
        if (generateBox) {
            generateBox.hidden = pdf;
        }
        dropHint.textContent = pdf
            ? "PDF \u00b7 up to " + MAX_UPLOAD_MB + " MB"
            : "JPEG or PNG \u00b7 up to " + MAX_UPLOAD_MB + " MB";
    }

    /** Assign a dropped/selected file to the input matching its type. */
    function acceptFile(file) {
        if (!file) {
            return false;
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
            return false;
        }

        if (MAX_BYTES && file.size > MAX_BYTES) {
            showMessage(
                "\u201c" + file.name + "\u201d is " + formatSize(file.size) +
                    ". The limit is " + MAX_UPLOAD_MB + " MB.",
                false
            );
            return false;
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
            return false;
        }

        showMessage("", true);
        renderPreview(file);
        return true;
    }

    // --- Upscaling --------------------------------------------------------

    function formatPixels(width, height) {
        return width + "\u00d7" + height;
    }

    function setUpscaling(busy) {
        upscaling = busy;
        upscaleBtn.disabled = busy;
        syncSubmitLock();
    }

    /**
     * Printing the current image while its replacement is on the way would
     * waste paper on exactly the print the replacement is meant to avoid.
     */
    function syncSubmitLock() {
        submitBtn.disabled = upscaling || generating;
    }

    /** Forget any upscale offer or request for the previous file. */
    function resetUpscale() {
        if (!upscaleBox) {
            return;
        }

        upscaleToken += 1;
        shownSize = null;
        if (upscaleRequest) {
            upscaleRequest.abort();
            upscaleRequest = null;
        }
        if (upscaling) {
            setUpscaling(false);
        }
        upscaleBox.hidden = true;
    }

    /** Ask the server whether this image is too small for A4. */
    function checkUpscale(width, height) {
        var url = window.LOCALPRINT && window.LOCALPRINT.upscalePlanUrl;
        if (!upscaleBox || !url || !width || !height) {
            return;
        }

        var token = upscaleToken;
        var request = new XMLHttpRequest();
        request.open(
            "GET",
            url + "?width=" + width + "&height=" + height
        );
        request.setRequestHeader("X-Requested-With", "LocalPrint");
        request.addEventListener("load", function () {
            if (token !== upscaleToken) {
                return;
            }

            var plan = null;
            try {
                plan = JSON.parse(request.responseText);
            } catch (err) {
                plan = null;
            }
            if (!plan || !plan.ok || !plan.low_res) {
                return;
            }

            shownSize = { width: width, height: height };
            upscaleNote.textContent =
                "Low resolution for A4: " + plan.dpi + " dpi. " +
                "It may print blurry.";

            var target = plan.target;
            upscaleBtn.hidden = !target;
            if (target) {
                upscaleBtn.textContent =
                    "Upscale " + formatPixels(width, height) +
                    " \u2192 " + formatPixels(target.width, target.height);
                upscaleBtn.title =
                    "About " + target.dpi + " dpi on A4";
            }
            upscaleBox.hidden = false;
        });
        request.send();
    }

    /** The server's JSON message from a failed blob request. */
    function readBlobError(request, fallback) {
        return new Promise(function (resolve) {
            fallback = fallback + " (HTTP " + request.status + ").";
            var blob = request.response;
            if (!blob || typeof blob.text !== "function") {
                resolve(fallback);
                return;
            }
            blob.text().then(function (text) {
                try {
                    resolve(JSON.parse(text).message || fallback);
                } catch (err) {
                    resolve(fallback);
                }
            }, function () {
                resolve(fallback);
            });
        });
    }

    function startUpscale() {
        var url = window.LOCALPRINT && window.LOCALPRINT.upscaleUrl;
        var file = imageInput.files && imageInput.files[0];
        if (!url || !file || !shownSize || upscaling) {
            return;
        }

        var token = upscaleToken;
        var original = shownSize;
        var label = upscaleBtn.textContent;

        setUpscaling(true);
        upscaleBtn.textContent = "Upscaling\u2026 this can take a minute";
        showMessage("", true);

        var data = new FormData();
        data.append("image", file);
        data.append("width", String(original.width));
        data.append("height", String(original.height));

        var request = new XMLHttpRequest();
        upscaleRequest = request;
        request.open("POST", url);
        request.responseType = "blob";
        request.setRequestHeader("X-Requested-With", "LocalPrint");

        request.addEventListener("load", function () {
            if (token !== upscaleToken) {
                return;
            }
            upscaleRequest = null;
            setUpscaling(false);

            if (request.status !== 200) {
                upscaleBtn.textContent = label;
                readBlobError(request, "Upscaling failed").then(function (message) {
                    if (token === upscaleToken) {
                        showMessage(message, false);
                    }
                });
                return;
            }

            var base = file.name.replace(/\.[^.]*$/, "") || "image";
            var upscaled = new File(
                [request.response],
                base + "-upscaled.jpg",
                { type: "image/jpeg" }
            );

            if (acceptFile(upscaled)) {
                showMessage(
                    "Upscaled from " +
                        formatPixels(original.width, original.height) +
                        " to " +
                        formatPixels(
                            request.getResponseHeader("X-Upscaled-Width"),
                            request.getResponseHeader("X-Upscaled-Height")
                        ) +
                        ". Ready to print.",
                    true
                );
            } else {
                upscaleBtn.textContent = label;
            }
        });

        request.addEventListener("error", function () {
            if (token !== upscaleToken) {
                return;
            }
            upscaleRequest = null;
            setUpscaling(false);
            upscaleBtn.textContent = label;
            showMessage("Could not reach the print server.", false);
        });

        request.send(data);
    }

    // --- Generating from a description ------------------------------------

    function setGenerating(busy) {
        generating = busy;
        generateBtn.disabled = busy;
        promptInput.readOnly = busy;
        generateBtn.textContent = busy
            ? "Generating\u2026 this can take a minute"
            : generateLabel;
        syncSubmitLock();
    }

    /** Abandon a generation the user has moved on from. */
    function cancelGenerate() {
        if (!generateBox) {
            return;
        }

        generateToken += 1;
        if (generateRequest) {
            generateRequest.abort();
            generateRequest = null;
        }
        if (generating) {
            setGenerating(false);
        }
    }

    function slugify(text) {
        var slug = text
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "")
            .slice(0, 40)
            .replace(/-+$/, "");
        return slug || "image";
    }

    function chosenOrientation() {
        var checked = generatePanel.querySelector(
            'input[name="generate_orientation"]:checked'
        );
        return checked ? checked.value : "portrait";
    }

    function startGenerate() {
        var url = window.LOCALPRINT && window.LOCALPRINT.generateUrl;
        var prompt = promptInput.value.trim();
        if (!url || generating) {
            return;
        }

        if (!prompt) {
            promptError.textContent = "Describe the image to generate.";
            promptError.hidden = false;
            promptInput.focus();
            return;
        }
        promptError.hidden = true;

        var token = generateToken;
        setGenerating(true);
        showMessage("", true);

        var data = new FormData();
        data.append("prompt", prompt);
        data.append("orientation", chosenOrientation());
        data.append("style", styleSelect.value);

        var request = new XMLHttpRequest();
        generateRequest = request;
        request.open("POST", url);
        request.responseType = "blob";
        request.setRequestHeader("X-Requested-With", "LocalPrint");

        request.addEventListener("load", function () {
            if (token !== generateToken) {
                return;
            }
            generateRequest = null;

            if (request.status !== 200) {
                setGenerating(false);
                readBlobError(request, "Image generation failed").then(
                    function (message) {
                        if (token === generateToken) {
                            showMessage(message, false);
                        }
                    }
                );
                return;
            }

            var generated = new File(
                [request.response],
                "generated-" + slugify(prompt) + ".jpg",
                { type: "image/jpeg" }
            );

            if (acceptFile(generated)) {
                generateLabel = "Generate again";
                showMessage(
                    "Image generated. Check the preview, then print.",
                    true,
                    true
                );
                // Bring the result into view once it has a height, so the
                // scroll lands where the picture actually is.
                var reveal = function () {
                    dropZone.scrollIntoView({ block: "start", behavior: "smooth" });
                };
                if (imageViewImg.complete && imageViewImg.naturalWidth) {
                    reveal();
                } else {
                    imageViewImg.addEventListener("load", reveal, { once: true });
                }
            }
            setGenerating(false);
        });

        request.addEventListener("error", function () {
            if (token !== generateToken) {
                return;
            }
            generateRequest = null;
            setGenerating(false);
            showMessage("Could not reach the print server.", false);
        });

        request.send(data);
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
        cancelGenerate();
        syncMode();
        clearFile();
    });
    modeImage.addEventListener("change", function () {
        cancelGenerate();
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

    // A file the user picks themselves wins over a generation in flight.
    pdfInput.addEventListener("change", function () {
        if (pdfInput.files[0]) {
            cancelGenerate();
            acceptFile(pdfInput.files[0]);
        }
    });
    imageInput.addEventListener("change", function () {
        if (imageInput.files[0]) {
            cancelGenerate();
            acceptFile(imageInput.files[0]);
        }
    });

    removeBtn.addEventListener("click", function (event) {
        event.stopPropagation();
        clearFile();
        showMessage("", true);
    });

    if (upscaleBtn) {
        upscaleBtn.addEventListener("click", startUpscale);
    }

    if (generateBox) {
        generateToggle.addEventListener("click", function () {
            var open = generatePanel.hidden;
            generatePanel.hidden = !open;
            generateToggle.setAttribute("aria-expanded", open ? "true" : "false");
            if (open) {
                promptInput.focus();
            }
        });

        generateBtn.addEventListener("click", startGenerate);

        promptInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                startGenerate();
            }
        });

        promptInput.addEventListener("input", function () {
            promptError.hidden = true;
        });
    }

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
            cancelGenerate();
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

    // Print options: keep the collapsed summary showing whatever differs
    // from the printer's own defaults, so nothing surprising stays hidden.
    var optionsBox = document.getElementById("options");
    if (optionsBox) {
        var optionsSummary = document.getElementById("options-summary");

        var describeOptions = function () {
            var changed = [];

            optionsBox.querySelectorAll(".option-field").forEach(function (field) {
                var dropdown = field.querySelector("select");
                var picked = dropdown
                    ? dropdown.selectedOptions[0]
                    : field.querySelector("input:checked");

                if (!picked || picked.dataset.default !== "no") {
                    return;
                }

                // A radio carries no text of its own; its label does.
                var text = dropdown
                    ? picked.textContent
                    : picked.closest("label").textContent;
                changed.push(text.trim());
            });

            optionsSummary.textContent = changed.length
                ? changed.join(" \u00b7 ")
                : "Printer defaults";
        };

        optionsBox.addEventListener("change", describeOptions);
        describeOptions();
    }

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

            // A job that reaches a stopped queue reports success, so the
            // queue has to be re-read before that success is believed.
            refreshQueue();
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
