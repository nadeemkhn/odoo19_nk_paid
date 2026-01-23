/** @odoo-module */

(function() {
    'use strict';
    
    let isHiding = false;
    let configCache = null;
    let configCheckCount = 0;
    let userSettingCache = null;
    let userSettingFetched = false;
    
    const fetchUserSetting = async () => {
        if (userSettingFetched) {
            return userSettingCache;
        }
        userSettingFetched = true;
        try {
            if (window.odoo && window.odoo.__DEBUG__ && window.odoo.__DEBUG__.services) {
                const orm = window.odoo.__DEBUG__.services.orm;
                if (orm && typeof orm.call === 'function') {
                    try {
                        const result = await orm.call('pos.session', 'get_user_hide_daily_sale_button_setting', []);
                        if (result && result.hide_daily_sale_button !== undefined) {
                            userSettingCache = result.hide_daily_sale_button;
                            return userSettingCache;
                        }
                    } catch (e) {
                        // Fallback to direct RPC
                    }
                }
            }
            
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
            const response = await fetch('/web/dataset/call_kw', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': csrfToken,
                },
                credentials: 'same-origin',
                body: JSON.stringify({
                    jsonrpc: '2.0',
                    method: 'call',
                    params: {
                        model: 'pos.session',
                        method: 'get_user_hide_daily_sale_button_setting',
                        args: [],
                        kwargs: {},
                        context: {},
                    },
                    id: Math.floor(Math.random() * 1000000000),
                }),
            });
            
            if (response.ok) {
                const data = await response.json();
                if (data.result && data.result.hide_daily_sale_button !== undefined) {
                    userSettingCache = data.result.hide_daily_sale_button;
                    return userSettingCache;
                }
            }
        } catch (error) {
            userSettingFetched = false;
        }
        return null;
    };
    
    const getPosConfig = () => {
        if (configCache && configCheckCount < 10) {
            configCheckCount++;
            return configCache;
        }
        
        const posApp = document.querySelector('pos-app') || 
                      document.querySelector('[class*="PosApp"]') ||
                      document.querySelector('[class*="pos-app"]') ||
                      document.body.querySelector('*');
        
        if (!posApp) {
            return null;
        }
        
        if (window.odoo && window.odoo.__DEBUG__) {
            const services = window.odoo.__DEBUG__.services;
            if (services && services.pos) {
                if (services.pos.config) {
                    configCache = services.pos.config;
                    return configCache;
                }
                if (services.pos.pos && services.pos.pos.config) {
                    configCache = services.pos.pos.config;
                    return configCache;
                }
            }
        }
        
        if (window.pos && window.pos.config) {
            configCache = window.pos.config;
            return configCache;
        }
        
        const allElements = document.querySelectorAll('*');
        for (const el of allElements) {
            if (el.__owl__) {
                if (el.__owl__.component?.pos?.config) {
                    configCache = el.__owl__.component.pos.config;
                    return configCache;
                }
                if (el.__owl__.app?.pos?.config) {
                    configCache = el.__owl__.app.pos.config;
                    return configCache;
                }
                if (el.__owl__.env?.pos?.config) {
                    configCache = el.__owl__.env.pos.config;
                    return configCache;
                }
                if (el.__owl__.ctx?.pos?.config) {
                    configCache = el.__owl__.ctx.pos.config;
                    return configCache;
                }
            }
        }
        
        const modal = document.querySelector('.modal, [role="dialog"], .close-pos-popup');
        if (modal && modal.__owl__) {
            const searchInOwl = (owl) => {
                if (owl.component?.props?.pos?.config) return owl.component.props.pos.config;
                if (owl.component?.pos?.config) return owl.component.pos.config;
                if (owl.component?.instance?.pos?.config) return owl.component.instance.pos.config;
                if (owl.ctx?.pos?.config) return owl.ctx.pos.config;
                if (owl.env?.pos?.config) return owl.env.pos.config;
                if (owl.component?.parent?.pos?.config) return owl.component.parent.pos.config;
                return null;
            };
            const found = searchInOwl(modal.__owl__);
            if (found) {
                configCache = found;
                return configCache;
            }
            let current = modal.__owl__;
            for (let i = 0; i < 5 && current; i++) {
                const found = searchInOwl(current);
                if (found) {
                    configCache = found;
                    return found;
                }
                current = current.component?.parent?.__owl__ || current.parent?.__owl__;
            }
        }
        
        return null;
    };
    
    const hideDailySaleButton = () => {
        if (isHiding) return;
        
        const config = getPosConfig();
        const shouldShow = config && (config.hide_daily_sale_button === true || config.hide_daily_sale_button === 'true');
        
        let buttons = document.querySelectorAll('button[data-daily-sale-button="true"]');
        if (buttons.length === 0) {
            buttons = document.querySelectorAll('button');
        }
        
        buttons.forEach(button => {
            const title = button.getAttribute('title') || '';
            const text = button.textContent.trim();
            const hasDataAttr = button.getAttribute('data-daily-sale-button') === 'true';
            
            if (hasDataAttr || title.includes('Download a report with all the sales of the current PoS Session') || 
                (text.includes('Daily Sale') && button.closest('.modal-footer, footer'))) {

                const showButtonAttr = button.getAttribute('data-show-button');
                let shouldShowButton = false;
                
                if (userSettingCache !== null) {
                    shouldShowButton = userSettingCache === true;
                }
                else if (showButtonAttr !== null && showButtonAttr !== '') {
                    shouldShowButton = showButtonAttr === 'true';
                }
                else if (config && config.hide_daily_sale_button !== undefined) {
                    shouldShowButton = config.hide_daily_sale_button === true || config.hide_daily_sale_button === 'true';
                }
                else if (!userSettingFetched) {
                    fetchUserSetting().then(setting => {
                        if (setting !== null) {
                            const shouldShow = setting === true;
                            if (!shouldShow) {
                                button.style.setProperty('display', 'none', 'important');
                                button.style.setProperty('visibility', 'hidden', 'important');
                                button.classList.add('d-none', 'hide-daily-sale-button');
                            } else {
                                button.style.setProperty('display', 'block', 'important');
                                button.style.setProperty('visibility', 'visible', 'important');
                                button.classList.remove('d-none', 'hide-daily-sale-button');
                            }
                            setTimeout(hideDailySaleButton, 100);
                        }
                    });
                    shouldShowButton = false;
                }
                else {
                    shouldShowButton = false;
                }
                
                if (!shouldShowButton) {
                    button.style.setProperty('display', 'none', 'important');
                    button.style.setProperty('visibility', 'hidden', 'important');
                    button.classList.add('d-none', 'hide-daily-sale-button');
                } else {
                    button.style.setProperty('display', 'block', 'important');
                    button.style.setProperty('visibility', 'visible', 'important');
                    button.style.setProperty('opacity', '1', 'important');
                    button.classList.remove('d-none', 'hide-daily-sale-button');
                    const currentStyle = button.getAttribute('style') || '';
                    if (currentStyle.includes('display: none')) {
                        button.setAttribute('style', currentStyle.replace(/display:\s*none[^;]*;?/gi, '').replace(/visibility:\s*hidden[^;]*;?/gi, ''));
                    }
                }
            }
        });
    };
    
    const observer = new MutationObserver(() => {
        hideDailySaleButton();
    });
    
    const startObserving = () => {
        const body = document.body;
        if (body && !observer._started) {
            observer.observe(body, {
                childList: true,
                subtree: true,
                attributes: false,
            });
            observer._started = true;
        }
    };
    
    fetchUserSetting();
    
    const checkInterval = setInterval(() => {
        hideDailySaleButton();
        startObserving();
    }, 500);
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            setTimeout(hideDailySaleButton, 100);
            setTimeout(hideDailySaleButton, 500);
            setTimeout(hideDailySaleButton, 1000);
            startObserving();
        });
    } else {
        setTimeout(hideDailySaleButton, 100);
        setTimeout(hideDailySaleButton, 500);
        setTimeout(hideDailySaleButton, 1000);
        startObserving();
    }
    
    document.addEventListener('click', () => {
        setTimeout(hideDailySaleButton, 100);
    }, true);
    
})();
