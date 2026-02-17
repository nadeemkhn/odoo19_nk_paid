/** @odoo-module **/

import { jsonrpc } from "@web/core/network/rpc_service";
import { localization } from "@web/core/l10n/localization";
import { insertThousandsSep } from "@web/core/utils/numbers";
const combinationInfoCache = new Map();

function parseQuantity(value, fallback = 1) {
    const qty = parseInt(value, 10);
    return Number.isInteger(qty) && qty > 0 ? qty : fallback;
}

function getProductCard(element) {
    return element.closest(".o_carousel_product_card, .oe_product_cart");
}

function getProductRoot(element) {
    return element.closest(".js_product") || getProductCard(element);
}

function getForm(root) {
    if (!root) {
        return null;
    }
    if (root.matches("form")) {
        return root;
    }
    return root.querySelector("form") || root.closest("form");
}

function ensureAddQtyInput(element, defaultQty) {
    const root = getProductRoot(element);
    const form = getForm(root);
    if (!form) {
        return null;
    }

    let qtyInput = form.querySelector('input[name="add_qty"]');
    if (!qtyInput) {
        qtyInput = document.createElement("input");
        qtyInput.type = "hidden";
        qtyInput.name = "add_qty";
        qtyInput.classList.add("o_qty_button_input");
        form.appendChild(qtyInput);
    }

    if (!qtyInput.value) {
        qtyInput.value = String(defaultQty);
    }

    return qtyInput;
}

function setButtonState(group, activeButton) {
    group.querySelectorAll(".o_qty_choice").forEach((button) => {
        const isActive = button === activeButton;
        button.classList.toggle("active", isActive);
        button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
}

function priceToStr(price, precision = 2) {
    const fixed = Number(price || 0).toFixed(precision).split(".");
    const { thousandsSep, decimalPoint, grouping } = localization;
    fixed[0] = insertThousandsSep(fixed[0], thousandsSep, grouping);
    return fixed.join(decimalPoint);
}

function getSelectedCombination(root) {
    if (!root?.classList?.contains("js_product")) {
        return [];
    }

    const selectedEls = root.querySelectorAll(
        "input.js_variant_change:checked, select.js_variant_change"
    );
    const values = [];
    selectedEls.forEach((el) => {
        const value = parseInt(el.value, 10);
        if (Number.isInteger(value) && value > 0) {
            values.push(value);
        }
    });
    return values;
}

function getGroupContext(group) {
    const root = getProductRoot(group);
    const form = getForm(root);
    const addButton = root?.querySelector(".js_add_cart");

    const asInt = (value) => {
        const parsed = parseInt(value, 10);
        return Number.isInteger(parsed) ? parsed : null;
    };

    const productTemplateId = asInt(
        form?.querySelector('input[name="product_template_id"]')?.value
        || addButton?.dataset.productTemplateId
        || group?.dataset.productTemplateId
    );
    const productId = asInt(
        form?.querySelector('input[name="product_id"]')?.value
        || addButton?.dataset.productId
        || group?.dataset.productId
    );
    const uomId = asInt(root?.querySelector('input[name="uom_id"]:checked')?.value);
    const combination = getSelectedCombination(root);

    return {
        root,
        form,
        productTemplateId,
        productId,
        uomId,
        combination,
    };
}

function getCacheKey(ctx, quantity) {
    return [
        ctx.productTemplateId || "",
        ctx.productId || "",
        ctx.uomId || "",
        (ctx.combination || []).join(","),
        quantity || 1,
    ].join("|");
}

async function fetchPriceInfo(ctx, quantity = 1) {
    const key = getCacheKey(ctx, quantity);
    if (combinationInfoCache.has(key)) {
        return combinationInfoCache.get(key);
    }

    const info = await jsonrpc("/website_sale/get_combination_info", {
        product_template_id: ctx.productTemplateId,
        product_id: ctx.productId,
        combination: ctx.combination,
        add_qty: quantity,
        uom_id: ctx.uomId,
    });

    combinationInfoCache.set(key, info);
    return info;
}

function findPriceContainer(root) {
    return root?.querySelector(".product_price");
}

function findSalePriceNode(priceContainer) {
    return (
        priceContainer?.querySelector(
            ".oe_price .oe_currency_value, .oe_price.oe_currency_value, [name='product_price'] .oe_currency_value, [aria-label='Sale price'] .oe_currency_value, .fw-bold .oe_currency_value, .oe_price, [aria-label='Sale price'], [name='product_price'], .mb-0.fw-bold"
        )
        || priceContainer?.querySelector(".oe_currency_value")
        || priceContainer?.querySelector(".h6, .h5, span")
    );
}

function findDefaultStrikeWrapper(priceContainer) {
    return priceContainer?.querySelector(".oe_default_price");
}

function findDefaultStrikeNode(priceContainer) {
    return findDefaultStrikeWrapper(priceContainer)?.querySelector(".oe_currency_value");
}

function findCompareStrikeWrapper(priceContainer) {
    return priceContainer?.querySelector(".oe_compare_list_price") || priceContainer?.querySelector("del");
}

function findCompareStrikeNode(priceContainer) {
    return findCompareStrikeWrapper(priceContainer)?.querySelector(".oe_currency_value");
}

function findSavingsNode(root) {
    return root?.querySelector(".o_qty_savings_text");
}

function setMonetaryText(node, value) {
    if (!node) {
        return;
    }
    const currencyNode = node.querySelector?.(".oe_currency_value");
    if (currencyNode) {
        currencyNode.textContent = value;
        return;
    }
    node.textContent = value;
}

function getMonetaryNodeText(node) {
    if (!node) {
        return "";
    }
    const currencyNode = node.querySelector?.(".oe_currency_value");
    return (currencyNode ? currencyNode.textContent : node.textContent || "").trim();
}

function normalizeNodeText(value) {
    return (value || "")
        .replaceAll("\u00a0", " ")
        .replace(/\s+/g, " ")
        .trim();
}

function getMonetaryAffixes(node) {
    const fullText = normalizeNodeText(node?.textContent);
    const numericText = normalizeNodeText(getMonetaryNodeText(node));
    if (!fullText || !numericText) {
        return { prefix: "", suffix: "" };
    }
    const idx = fullText.indexOf(numericText);
    if (idx === -1) {
        return { prefix: "", suffix: "" };
    }
    return {
        prefix: fullText.slice(0, idx),
        suffix: fullText.slice(idx + numericText.length),
    };
}

function formatMonetaryLikeNode(node, amount, precision) {
    const formattedNumber = priceToStr(amount, precision);
    const { prefix, suffix } = getMonetaryAffixes(node);
    if (!prefix && !suffix) {
        return formattedNumber;
    }
    return `${prefix}${formattedNumber}${suffix}`.trim();
}

function parseMonetaryNodeValue(node) {
    const raw = getMonetaryNodeText(node);
    if (!raw) {
        return null;
    }
    const { thousandsSep, decimalPoint } = localization;
    let normalized = raw
        .replaceAll("\u00a0", "")
        .replaceAll(" ", "")
        .replaceAll(thousandsSep || ",", "")
        .replaceAll(decimalPoint || ".", ".");
    normalized = normalized.replace(/[^\d.-]/g, "");
    const parsed = parseFloat(normalized);
    return Number.isFinite(parsed) ? parsed : null;
}

function applyInstantPriceScale(root, previousQuantity, nextQuantity) {
    if (!root || !previousQuantity || previousQuantity < 1 || !nextQuantity || nextQuantity < 1) {
        return;
    }
    if (previousQuantity === nextQuantity) {
        return;
    }

    const priceContainer = findPriceContainer(root);
    if (!priceContainer) {
        return;
    }

    const salePriceNode = findSalePriceNode(priceContainer);
    const salePriceValue = parseMonetaryNodeValue(salePriceNode);
    if (salePriceNode && salePriceValue !== null) {
        const estimatedTotal = (salePriceValue / previousQuantity) * nextQuantity;
        setMonetaryText(
            salePriceNode,
            formatMonetaryLikeNode(salePriceNode, estimatedTotal),
        );
    }

    const defaultStrikeNode = findDefaultStrikeNode(priceContainer);
    const defaultStrikeValue = parseMonetaryNodeValue(defaultStrikeNode);
    if (defaultStrikeNode && defaultStrikeValue !== null) {
        const estimatedStrike = (defaultStrikeValue / previousQuantity) * nextQuantity;
        setMonetaryText(
            defaultStrikeNode,
            formatMonetaryLikeNode(defaultStrikeNode, estimatedStrike),
        );
    }

    const compareStrikeNode = findCompareStrikeNode(priceContainer);
    const compareStrikeValue = parseMonetaryNodeValue(compareStrikeNode);
    if (compareStrikeNode && compareStrikeValue !== null) {
        const estimatedCompare = (compareStrikeValue / previousQuantity) * nextQuantity;
        setMonetaryText(
            compareStrikeNode,
            formatMonetaryLikeNode(compareStrikeNode, estimatedCompare),
        );
    }
}

function updateDisplayedPrices(root, quantityInfo, quantity = 1) {
    const priceContainer = findPriceContainer(root);
    if (!priceContainer) {
        return;
    }

    const precision = Number.isInteger(quantityInfo.currency_precision)
        ? quantityInfo.currency_precision
        : 2;
    const normalizedQty = parseQuantity(quantity, 1);
    const totalPrice = Number(quantityInfo.price || 0) * normalizedQty;

    const salePriceNode = findSalePriceNode(priceContainer);
    if (salePriceNode) {
        setMonetaryText(
            salePriceNode,
            formatMonetaryLikeNode(salePriceNode, totalPrice, precision),
        );
    }

    const hasComparePrice = Boolean(
        quantityInfo.compare_list_price && quantityInfo.compare_list_price > quantityInfo.price
    );
    const hasDiscountPrice = Boolean(!hasComparePrice && quantityInfo.has_discounted_price);

    const defaultStrikeWrapper = findDefaultStrikeWrapper(priceContainer);
    const defaultStrikeNode = findDefaultStrikeNode(priceContainer);
    const compareStrikeWrapper = findCompareStrikeWrapper(priceContainer);
    const compareStrikeNode = findCompareStrikeNode(priceContainer);

    if (hasComparePrice) {
        if (compareStrikeWrapper && compareStrikeNode) {
            compareStrikeWrapper.classList.remove("d-none");
            setMonetaryText(
                compareStrikeNode,
                formatMonetaryLikeNode(
                    compareStrikeNode,
                    Number(quantityInfo.compare_list_price || 0) * normalizedQty,
                    precision,
                ),
            );
        }
        if (defaultStrikeWrapper && defaultStrikeWrapper !== compareStrikeWrapper) {
            defaultStrikeWrapper.classList.add("d-none");
        }
    } else if (hasDiscountPrice) {
        if (defaultStrikeWrapper && defaultStrikeNode) {
            defaultStrikeWrapper.classList.remove("d-none");
            setMonetaryText(
                defaultStrikeNode,
                formatMonetaryLikeNode(
                    defaultStrikeNode,
                    Number(quantityInfo.list_price || 0) * normalizedQty,
                    precision,
                ),
            );
        }
        if (compareStrikeWrapper && compareStrikeWrapper !== defaultStrikeWrapper) {
            compareStrikeWrapper.classList.add("d-none");
        }
    } else {
        defaultStrikeWrapper?.classList.add("d-none");
        if (compareStrikeWrapper && compareStrikeWrapper !== defaultStrikeWrapper) {
            compareStrikeWrapper.classList.add("d-none");
        }
    }
}

function getDiscountPercent(unitPrice, qtyUnitPrice) {
    const baseline = Number(unitPrice || 0);
    const actual = Number(qtyUnitPrice || 0);
    if (!(baseline > 0) || !(actual >= 0) || actual >= baseline) {
        return 0;
    }
    return Math.round(((baseline - actual) / baseline) * 100);
}

function updateSavingsText(root, quantity, unitInfo, quantityInfo) {
    const savingsNode = findSavingsNode(root);
    if (!savingsNode) {
        return;
    }

    const qty = parseQuantity(quantity, 1);
    if (qty <= 1) {
        savingsNode.classList.add("d-none");
        savingsNode.textContent = "";
        return;
    }

    const baselineTotal = Number(unitInfo.price || 0) * qty;
    const selectedTotal = Number(quantityInfo.price || 0) * qty;
    const savingsAmount = baselineTotal - selectedTotal;
    if (!(baselineTotal > 0) || !(savingsAmount > 0)) {
        savingsNode.classList.add("d-none");
        savingsNode.textContent = "";
        return;
    }

    const percent = Math.round((savingsAmount / baselineTotal) * 100);
    if (!(percent > 0)) {
        savingsNode.classList.add("d-none");
        savingsNode.textContent = "";
        return;
    }

    const precision = Number.isInteger(quantityInfo.currency_precision)
        ? quantityInfo.currency_precision
        : 2;
    const salePriceNode = findSalePriceNode(findPriceContainer(root));
    const savingsLabel = formatMonetaryLikeNode(salePriceNode, savingsAmount, precision);
    savingsNode.textContent = `Save ${savingsLabel} (${percent}%)`;
    savingsNode.classList.remove("d-none");
}

async function updateDiscountBadges(group, ctx, unitInfo, requestToken) {
    const buttons = [...group.querySelectorAll(".o_qty_choice")];
    await Promise.all(buttons.map(async (button) => {
        const badge = button.querySelector(".o_qty_discount_badge");
        if (!badge) {
            return;
        }

        const qty = parseQuantity(button.dataset.qty, 1);
        if (qty <= 1) {
            badge.classList.add("d-none");
            badge.textContent = "";
            return;
        }

        const qtyInfo = await fetchPriceInfo(ctx, qty);
        if (ctx.root?.dataset.qtyPriceRequestToken !== requestToken) {
            return;
        }

        const percent = getDiscountPercent(unitInfo.price, qtyInfo.price);
        if (percent > 0) {
            badge.textContent = `${percent}%`;
            badge.classList.remove("d-none");
        } else {
            badge.classList.add("d-none");
            badge.textContent = "";
        }
    }));
}

async function refreshPriceByQuantity(group, quantity) {
    const ctx = getGroupContext(group);
    if (!ctx.root || !ctx.productTemplateId) {
        return;
    }

    const requestToken = `${Date.now()}_${Math.random()}`;
    ctx.root.dataset.qtyPriceRequestToken = requestToken;
    ctx.root.classList.add("o_qty_price_loading");

    try {
        const [unitInfo, selectedQtyInfo] = await Promise.all([
            fetchPriceInfo(ctx, 1),
            fetchPriceInfo(ctx, quantity),
        ]);
        if (ctx.root.dataset.qtyPriceRequestToken !== requestToken) {
            return;
        }
        updateDisplayedPrices(ctx.root, selectedQtyInfo, quantity);
        updateSavingsText(ctx.root, quantity, unitInfo, selectedQtyInfo);
        await updateDiscountBadges(group, ctx, unitInfo, requestToken);
    } catch {
        // Keep current DOM price if request fails.
    } finally {
        if (ctx.root.dataset.qtyPriceRequestToken === requestToken) {
            ctx.root.classList.remove("o_qty_price_loading");
        }
    }
}

function applySelectedQuantity(group, quantity) {
    const root = getProductRoot(group);
    const previousQuantity = parseQuantity(
        root?.querySelector("input[name='add_qty']")?.value
        || root?.dataset.selectedQtyButton
        || group.dataset.selectedQtyButton,
        1,
    );
    if (
        root
        && previousQuantity === quantity
        && quantity > 1
        && root.dataset.qtyPriceInitialized !== "1"
    ) {
        // Initial server-rendered website price is per 1 qty; scale immediately on first paint.
        applyInstantPriceScale(root, 1, quantity);
    }
    applyInstantPriceScale(root, previousQuantity, quantity);

    const qtyInput = ensureAddQtyInput(group, quantity);
    if (qtyInput) {
        qtyInput.value = String(quantity);
        // On product detail page, core variant JS rewrites price to per-unit on add_qty change.
        // We avoid that extra rewrite to prevent flicker and keep quantity-total pricing stable.
        const isProductDetail = Boolean(root?.classList?.contains("js_product"));
        if (!isProductDetail) {
            qtyInput.dispatchEvent(new Event("change", { bubbles: true }));
        }
    }

    const card = getProductCard(group);
    if (card) {
        card.dataset.selectedQtyButton = String(quantity);
    }
    if (root) {
        root.dataset.qtyPriceInitialized = "1";
    }
    group.dataset.selectedQtyButton = String(quantity);

    refreshPriceByQuantity(group, quantity);
    // Re-apply once more because core variant mixin also updates price asynchronously.
    setTimeout(() => refreshPriceByQuantity(group, quantity), 220);
}

function getActiveQuantityForGroup(group) {
    return parseQuantity(
        group?.querySelector(".o_qty_choice.active")?.dataset.qty
        || group?.dataset.defaultQty
        || group?.closest(".js_product")?.querySelector("input[name='add_qty']")?.value,
        1,
    );
}

function initializeQuantityGroup(group) {
    if (group.dataset.qtyButtonsInitialized === "1") {
        return;
    }
    group.dataset.qtyButtonsInitialized = "1";

    const defaultButton = group.querySelector(".o_qty_choice.o_qty_default")
        || group.querySelector(".o_qty_choice");
    if (!defaultButton) {
        return;
    }

    const quantity = parseQuantity(defaultButton.dataset.qty, parseQuantity(group.dataset.defaultQty, 1));
    setButtonState(group, defaultButton);
    applySelectedQuantity(group, quantity);
}

function initializeQuantityGroups(root) {
    root.querySelectorAll(".o_qty_button_group").forEach((group) => initializeQuantityGroup(group));
}

document.addEventListener("click", (ev) => {
    const quantityButton = ev.target.closest(".o_qty_choice");
    if (quantityButton) {
        const group = quantityButton.closest(".o_qty_button_group");
        if (!group) {
            return;
        }
        const quantity = parseQuantity(quantityButton.dataset.qty, 1);
        setButtonState(group, quantityButton);
        applySelectedQuantity(group, quantity);
        return;
    }
}, true);

document.addEventListener("change", (ev) => {
    if (ev.target.matches(".js_product input.js_variant_change, .js_product select.js_variant_change")) {
        const group = ev.target.closest(".js_product")?.querySelector(".o_qty_button_group");
        if (!group) {
            return;
        }
        const quantity = parseQuantity(
            group.querySelector(".o_qty_choice.active")?.dataset.qty || group.dataset.defaultQty,
            1,
        );
        setTimeout(() => refreshPriceByQuantity(group, quantity), 220);
        return;
    }

    if (ev.target.matches(".js_product input[name='add_qty']")) {
        const group = ev.target.closest(".js_product")?.querySelector(".o_qty_button_group");
        if (!group) {
            return;
        }
        const quantity = parseQuantity(ev.target.value, 1);
        setTimeout(() => refreshPriceByQuantity(group, quantity), 220);
        return;
    }

    if (ev.target.matches(".js_product input.product_id")) {
        // Core variant flow updates this at the end of combination refresh.
        const group = ev.target.closest(".js_product")?.querySelector(".o_qty_button_group");
        if (!group) {
            return;
        }
        const quantity = getActiveQuantityForGroup(group);
        setTimeout(() => refreshPriceByQuantity(group, quantity), 30);
    }
}, true);

initializeQuantityGroups(document);

const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
            if (!(node instanceof HTMLElement)) {
                return;
            }
            if (node.matches(".o_qty_button_group")) {
                initializeQuantityGroup(node);
            } else {
                initializeQuantityGroups(node);
            }
        });
    });
});

if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
}
