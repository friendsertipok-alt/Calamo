    function getCookie(name) {
        let matches = document.cookie.match(new RegExp(
            "(?:^|; )" + name.replace(/([\.$?*|{}\(\)\[\]\\\/\+^])/g, '\\$1') + "=([^;]*)"
        ));
        return matches ? decodeURIComponent(matches[1]) : undefined;
    }

    function isUserAuthenticated() {
        console.log("Checking auth: currentUser=", !!currentUser);
        // Если объект пользователя уже загружен — мы точно в аккаунте
        if (currentUser) return true;
        // Приоритет куке, так как она ставится бэкендом
        const status = getCookie('logged_in_status');
        console.log("Checking auth: cookie logged_in_status=", status);
        if (status === 'true') return true;
        // Резерв - старый токен
        return !!(authToken && authToken !== 'null');
    }

    // Экспонируем функции в глобальную область для HTML (onclick и т.д.)
    window.openProfile = openProfile;
    window.showAuthModal = showAuthModal;
    window.logout = logout;
    window.isUserAuthenticated = isUserAuthenticated;

    async function apiFetch(url, options = {}) {
        options.credentials = "include"; // Всегда включаем куки
        if (!options.headers) options.headers = {};
        options.headers['X-Requested-With'] = 'XMLHttpRequest';
        
        // Если есть старый токен в localStorage, добавляем его для совместимости,
        // но приоритет у кук бэкенда.
        let token = localStorage.getItem('auth_token');
        if (token && token !== 'null') {
            options.headers['Authorization'] = `Bearer ${token}`;
        }
        
        return fetch(url, options);
    }

    let authToken = localStorage.getItem('auth_token');
    let currentUser = null;
    let currentPrice = 3500;
    let currentStep = 1;
    let volumeType = 'pages';
    let currentOrderId = localStorage.getItem('activeOrderId');
    let pollInterval = null;
    let draftData = null;
    let isDraftMode = false;

    // === КОНФИГУРАЦИЯ ===
    const API_BASE_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' 
        ? 'http://127.0.0.1:8000/api' 
        : window.location.origin + '/api';

    const TOTAL_STEPS = 5;

    function updateSupportFileLabel(input) {
        const label = document.getElementById('sup-file-label');
        if (input.files && input.files.length > 0) {
            label.innerHTML = `
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" style="margin-bottom: 8px;"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                <div style="color: var(--text-main); font-weight: 600;">Выбрано файлов: ${input.files.length}</div>
            `;
            label.style.borderColor = 'var(--accent)';
            label.style.background = 'rgba(255, 77, 0, 0.05)';
        } else {
            label.innerHTML = `
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-bottom: 8px; opacity: 0.5;"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
                <div>Нажмите для выбора файлов</div>
            `;
            label.style.borderColor = 'var(--border)';
            label.style.background = 'rgba(255,255,255,0.03)';
        }
    }
    const PRICES = {
        'реферат': { amount: 1000, label: 'реферат' },
        'дтз': { amount: 1500, label: 'домашнее творческое задание' },
        'курсовая': { amount: 3500, label: 'курсовую работу' },
        'диплом': { amount: 7000, label: 'дипломную работу' },
    };

    function openForm() {
        document.getElementById('landing-view').style.display = 'none';
        document.getElementById('profile-view').style.display = 'none';
        document.getElementById('form-view').style.display = 'block';
        window.scrollTo(0, 0);
        
        // Сбрасываем режимы, так как это НОВЫЙ заказ
        isDraftMode = false;
        draftData = null;
        currentStep = 1;

        // Возвращаем видимость полей для шага 4 (если они были скрыты редактором)
        const matInput = document.getElementById('materials-input');
        const matEditor = document.getElementById('materials-editor');
        if (matInput) matInput.style.display = 'block';
        if (matEditor) matEditor.style.display = 'none';

        updateWizard();
        updatePrice();
    }


    // (Duplicates removed)
    async function resumeOrder(id) {
        currentOrderId = id;
        localStorage.setItem('activeOrderId', id);
        
        document.getElementById('landing-view').style.display = 'none';
        document.getElementById('form-view').style.display = 'block';
        
        try {
            const res = await fetch(`${API_BASE_URL}/orders/${id}`);
            if (!res.ok) throw new Error('Заказ не найден');
            const data = await res.json();
            
            if (['processing', 'completed', 'generating', 'generating_text', 'generating_charts'].includes(data.status)) {
                startPolling(id);
                return;
            }
            
            if (data.status === 'draft_ready') {
                renderDraftReady(data);
            } else {
                currentStep = 3; // По умолчанию на шаг правок если есть черновик
                updateWizard();
            }
        } catch (e) {
            console.error("Resume error:", e);
            localStorage.removeItem('activeOrderId');
            openLanding();
        }
    }

    // === НАВИГАЦИЯ ЭКРАНОВ ===
    function openLanding() {
        document.getElementById('form-view').style.display = 'none';
        document.getElementById('profile-view').style.display = 'none';
        document.getElementById('status-view').style.display = 'none';
        document.getElementById('landing-view').style.display = 'block';
        document.querySelector('header').style.display = 'flex'; // Always show
        window.scrollTo(0, 0);
        if(pollInterval) clearInterval(pollInterval);
        checkActiveOrder();
    }

    // Показываем кнопку "Продолжить" на главном экране если есть активный заказ
    function checkActiveOrder() {
        const activeId = localStorage.getItem('activeOrderId');
        const resumeBox = document.getElementById('hero-resume-box');
        if (activeId && activeId !== 'null' && !pollInterval) {
            resumeBox.style.display = 'block';
            resumeBox.innerHTML = `
                <div class="resume-banner" style="background: rgba(255,123,0,0.1); border: 1px solid rgba(255,123,0,0.2); padding: 20px; border-radius: 20px; display: flex; align-items: center; justify-content: space-between; gap: 20px; backdrop-filter: blur(10px); animation: modalPop 0.5s ease-out;">
                    <div style="display: flex; align-items: center; gap: 15px;">
                        <div style="width: 45px; height: 45px; background: var(--accent-light); border-radius: 12px; display: flex; align-items: center; justify-content: center; color: white;">
                            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
                        </div>
                        <div style="text-align: left;">
                            <div style="font-weight: 800; color: var(--text-main); font-size: 16px; letter-spacing: -0.5px;">У вас есть активная работа</div>
                            <div style="font-size: 13px; color: var(--text-muted); font-weight: 500;">Продолжите генерацию прямо сейчас</div>
                        </div>
                    </div>
                    <button class="btn-primary" style="padding: 12px 24px; font-size: 14px; margin: 0;" onclick="resumeOrder('${activeId}')">
                        Продолжить
                    </button>
                </div>
            `;
        } else {
            if(resumeBox) resumeBox.style.display = 'none';
        }
    }

    function openProfile() {
        if (!isUserAuthenticated()) return showAuthModal();
        document.getElementById('landing-view').style.display = 'none';
        document.getElementById('form-view').style.display = 'none';
        document.getElementById('status-view').style.display = 'none';
        document.getElementById('profile-view').style.display = 'block';
        document.getElementById('profile-view').style.paddingTop = '120px';
        document.querySelector('header').style.display = 'flex'; // Always show
        if(pollInterval) clearInterval(pollInterval);
        fetchProfile();
        fetchMyWorks();
    }

    function showAuthModal() { document.getElementById('auth-modal').style.display = 'flex'; }
    function closeAuthModal() { document.getElementById('auth-modal').style.display = 'none'; }

    function openForm() {
        document.getElementById('landing-view').style.display = 'none';
        document.getElementById('form-view').style.display = 'block';
        document.getElementById('status-view').style.display = 'none';
        document.getElementById('profile-view').style.display = 'none';
        document.querySelector('header').style.display = 'flex'; // Always show
        window.scrollTo(0, 0);
        
        // Сбрасываем режимы, так как это НОВЫЙ заказ
        isDraftMode = false;
        draftData = null;
        currentStep = 1;

        // Возвращаем видимость полей для шага 4 (если они были скрыты редактором)
        const matInput = document.getElementById('materials-input');
        const matEditor = document.getElementById('materials-editor');
        if (matInput) matInput.style.display = 'block';
        if (matEditor) matEditor.style.display = 'none';

        updateWizard();
        updatePrice();
    }

    // === ЦЕНООБРАЗОВАНИЕ ===
    function updatePrice() {
        const typeEl = document.getElementById('f-work_type');
        if (!typeEl) return;
        
        const type = typeEl.value;
        const info = PRICES[type] || { amount: 3500, label: 'курсовую работу' };
        currentPrice = info.amount;

        const formatted = info.amount.toLocaleString('ru-RU') + ' ₽';
        const priceVal = document.getElementById('display-price');
        if (priceVal) priceVal.textContent = formatted;
        
        const priceLabel = document.querySelector('.price-label');
        if (priceLabel) priceLabel.textContent = 'Стоимость за ' + info.label;
        
        const payEl = document.getElementById('pay-amount');
        if (payEl) payEl.textContent = formatted;
    }

    // === ПЕРЕКЛЮЧЕНИЕ ОБЪЁМА ===
    window.validateWizardLimits = validateWizardLimits;

    function validateWizardLimits() {
        const volumeInput = document.getElementById('f-volume');
        const imagesInput = document.getElementById('f-images');
        const tablesInput = document.getElementById('f-tables');
        const btnNext = document.getElementById('btn-next');
        
        let isValid = true;
        
        if (volumeInput) {
            const val = parseInt(volumeInput.value) || 0;
            const min = parseInt(volumeInput.getAttribute('min')) || 15;
            const max = parseInt(volumeInput.getAttribute('max')) || 60;
            if (val < min || val > max) {
                volumeInput.style.setProperty('border', '1px solid #ef4444', 'important');
                volumeInput.style.setProperty('background', 'rgba(239, 68, 68, 0.05)', 'important');
                isValid = false;
            } else {
                volumeInput.style.removeProperty('border');
                volumeInput.style.removeProperty('background');
            }
        }
        
        if (imagesInput) {
            const val = parseInt(imagesInput.value) || 0;
            const min = parseInt(imagesInput.getAttribute('min')) || 0;
            const max = parseInt(imagesInput.getAttribute('max')) || 10;
            if (val < min || val > max) {
                imagesInput.style.setProperty('border', '1px solid #ef4444', 'important');
                imagesInput.style.setProperty('background', 'rgba(239, 68, 68, 0.05)', 'important');
                isValid = false;
            } else {
                imagesInput.style.removeProperty('border');
                imagesInput.style.removeProperty('background');
            }
        }
        
        if (tablesInput) {
            const val = parseInt(tablesInput.value) || 0;
            const min = parseInt(tablesInput.getAttribute('min')) || 0;
            const max = parseInt(tablesInput.getAttribute('max')) || 13;
            if (val < min || val > max) {
                tablesInput.style.setProperty('border', '1px solid #ef4444', 'important');
                tablesInput.style.setProperty('background', 'rgba(239, 68, 68, 0.05)', 'important');
                isValid = false;
            } else {
                tablesInput.style.removeProperty('border');
                tablesInput.style.removeProperty('background');
            }
        }
        
        if (btnNext && currentStep === 1) {
            if (!isValid) {
                btnNext.disabled = true;
                btnNext.style.setProperty('background', 'rgba(255,255,255,0.05)', 'important');
                btnNext.style.setProperty('color', '#888888', 'important');
                btnNext.style.setProperty('cursor', 'not-allowed', 'important');
                btnNext.style.setProperty('box-shadow', 'none', 'important');
            } else {
                btnNext.disabled = false;
                btnNext.style.removeProperty('background');
                btnNext.style.removeProperty('color');
                btnNext.style.removeProperty('cursor');
                btnNext.style.removeProperty('box-shadow');
            }
        }
    }

    function setVolumeType(type) {
        volumeType = type;
        const btnPages = document.getElementById('btn-pages');
        const btnWords = document.getElementById('btn-words');
        const volumeInput = document.getElementById('f-volume');
        const volLabel = document.getElementById('limit-volume-label');
        
        if (type === 'pages') {
            if (btnPages) btnPages.classList.add('active');
            if (btnWords) btnWords.classList.remove('active');
            if (volumeInput) {
                volumeInput.setAttribute('min', '15');
                volumeInput.setAttribute('max', '60');
                volumeInput.value = "35";
            }
            if (volLabel) volLabel.textContent = "от 15 до 60 стр.";
        } else {
            if (btnWords) btnWords.classList.add('active');
            if (btnPages) btnPages.classList.remove('active');
            if (volumeInput) {
                volumeInput.setAttribute('min', '3750');
                volumeInput.setAttribute('max', '15000');
                volumeInput.value = "9000";
            }
            if (volLabel) volLabel.textContent = "от 3750 до 15000 слов";
        }
        updatePrice();
        validateWizardLimits();
    }

    // === СИНХРОНИЗАЦИЯ ОБЪЁМА ===
    function syncVolume(source) {
        const pagesInput = document.getElementById('f-pages_count');
        const wordsInput = document.getElementById('f-target_words');
        const ratio = 250; // 1 страница ≈ 250 слов

        if (source === 'pages') {
            const p = parseInt(pagesInput.value) || 0;
            wordsInput.value = p * ratio;
        } else {
            const w = parseInt(wordsInput.value) || 0;
            pagesInput.value = Math.round(w / ratio);
        }
    }

    // === ВИЗАРД: НАВИГАЦИЯ ===
    function updateWizard() {
        document.querySelectorAll('.wizard-step').forEach(el => el.style.display = 'none');
        const stepEl = document.getElementById('step-' + currentStep);
        if (stepEl) stepEl.style.display = 'block';

        document.querySelectorAll('.step').forEach((el, idx) => {
            const stepNum = idx + 1;
            if (stepNum <= currentStep) el.classList.add('active');
            else el.classList.remove('active');
            
            let numBlock = el.querySelector('.step-number');
            if (numBlock) {
                if (stepNum < currentStep) {
                    numBlock.innerHTML = '✓';
                    numBlock.style.background = 'var(--success)';
                } else {
                    numBlock.innerHTML = stepNum;
                    numBlock.style.background = stepNum === currentStep ? 'var(--accent-light)' : 'rgba(255,255,255,0.1)';
                }
            }
        });

        const btnPrev = document.getElementById('btn-prev');
        // Скрываем назад на 1-м шаге, на шаге генерации плана (2) и на финальном шаге (5)
        if (btnPrev) btnPrev.style.visibility = (currentStep > 1 && currentStep !== 2 && currentStep !== 5) ? 'visible' : 'hidden';
        
        const isPayStep = currentStep === 4;
        const isGenStep = currentStep === 2;
        
        const btnNext = document.getElementById('btn-next');
        const btnSubmit = document.getElementById('btn-submit');

        if (btnNext) {
            if (currentStep === 3) {
                btnNext.style.display = 'inline-flex';
                btnNext.innerHTML = 'Подтвердить и далее <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left: 8px;"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>';
            } else if (isPayStep || isGenStep || currentStep === TOTAL_STEPS) {
                btnNext.style.display = 'none';
                btnNext.onclick = nextStep; // Reset to default
            } else {
                btnNext.style.display = 'inline-flex';
                btnNext.innerHTML = 'Далее <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left: 8px;"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>';
                btnNext.onclick = nextStep; // Reset to default
            }
        }
        if (btnSubmit) btnSubmit.style.display = (currentStep === TOTAL_STEPS) ? 'inline-flex' : 'none';

        // Оплата: проверяем баланс
        if (currentStep === 4) {
            const userBalance = currentUser ? currentUser.balance : 0;
            const btnBalance = document.getElementById('btn-pay-balance');
            const btnExternal = document.getElementById('btn-pay-trigger');
            
            if (btnBalance && btnExternal) {
                if (userBalance >= currentPrice) {
                    btnBalance.style.display = 'block';
                    btnBalance.innerHTML = `Списать ${currentPrice.toLocaleString()} ₽ с баланса`;
                    
                    // Второстепенная кнопка (белая)
                    btnExternal.style.background = 'rgba(255,255,255,0.05)';
                    btnExternal.style.color = 'var(--text-muted)';
                    btnExternal.style.boxShadow = 'none';
                    btnExternal.innerHTML = 'Пополнить баланс / Карта';
                } else {
                    btnBalance.style.display = 'none';
                    
                    // Главная кнопка (теперь она белая, если баланса нет)
                    btnExternal.style.background = '#fff';
                    btnExternal.style.color = '#000';
                    btnExternal.style.boxShadow = '0 10px 30px rgba(255,255,255,0.1)';
                    btnExternal.innerHTML = `Пополнить на ${(currentPrice - userBalance).toLocaleString()} ₽`;
                }
            }
        }

        if (currentStep === 5) {
            confetti({
                particleCount: 150,
                spread: 70,
                origin: { y: 0.6 },
                colors: ['#f97316', '#fb923c', '#ffffff']
            });
        }
        
        if (currentStep === 1) {
            validateWizardLimits();
        }
    }

    async function nextStep() {
        if (currentStep === 1) {
            const topic = document.getElementById('f-topic').value;
            const subject = document.getElementById('f-subject').value;
            if (!topic || !subject) return alert('Заполните обязательные поля');
            
            if (volumeType === 'pages') {
                const p = parseInt(document.getElementById('f-volume').value) || 0;
                if (p < 15 || p > 60) {
                    alert('Количество страниц должно быть в пределах от 15 до 60.');
                    return;
                }
            } else {
                const w = parseInt(document.getElementById('f-volume').value) || 0;
                if (w < 3750 || w > 15000) {
                    alert('Количество слов должно быть в пределах от 3750 до 15000.');
                    return;
                }
            }
            const figs = parseInt(document.getElementById('f-images').value) || 0;
            if (figs < 0 || figs > 10) {
                alert('Количество рисунков должно быть в пределах от 0 до 10.');
                return;
            }
            const tabs = parseInt(document.getElementById('f-tables').value) || 0;
            if (tabs < 0 || tabs > 13) {
                alert('Количество таблиц должно быть в пределах от 0 до 13.');
                return;
            }
            
            currentStep = 2;
            updateWizard();
            submitOrder();
            return;
        }
        
        if (currentStep === 3) {
            // Если мы на шаге правок, то при нажатии "Далее" мы выходим из режима черновика
            isDraftMode = false;
        }

        if (currentStep < TOTAL_STEPS) currentStep++;
        updateWizard();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function prevStep() {
        if (currentStep === 3) {
            if(confirm("Вы уверены, что хотите вернуться на первый шаг? Сгенерированный черновик будет потерян.")) {
                currentStep = 1;
                isDraftMode = false;
                localStorage.removeItem('activeOrderId');
                currentOrderId = null;
            } else {
                return;
            }
        }
        if (currentStep === 5) return; // Нельзя уйти с финала
        if (currentStep > 1) {
            currentStep--;
        }
        updateWizard();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // === ОПЛАТА (БАЛАНС) ===
    async function payFromBalance() {
        // Можем быть вызваны как из центральной кнопки, так и из кнопки "Далее" в футере
        const balanceBtn = document.getElementById('btn-pay-balance');
        const nextBtn = document.getElementById('btn-next');
        
        const btns = [balanceBtn, nextBtn].filter(b => b && b.style.display !== 'none');
        const originalTexts = btns.map(b => b.innerHTML);
        
        btns.forEach(b => {
            b.disabled = true;
            b.innerHTML = '⌛ Списание...';
        });
        
        try {
            const res = await apiFetch(`${API_BASE_URL}/generation/${currentOrderId}/confirm_balance`, {
                method: 'POST',
                headers: { 
                    'Authorization': `Bearer ${authToken}`,
                    'Content-Type': 'application/json'
                }
            });
            
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Ошибка при списании');
            }
            
            currentStep = 5;
            updateWizard();
        } catch (e) {
            alert(e.message);
            btns.forEach((b, i) => {
                b.disabled = false;
                b.innerHTML = originalTexts[i];
            });
        }
    }

    // === CUSTOM SELECT INIT ===
    function initCustomSelects() {
        const wrappers = document.querySelectorAll('.custom-select-wrapper');
        wrappers.forEach(wrapper => {
            const trigger = wrapper.querySelector('.custom-select-trigger');
            const options = wrapper.querySelectorAll('.custom-option');
            const hiddenInput = wrapper.querySelector('input[type="hidden"]');

            if (!trigger || !options.length || !hiddenInput) return;

            trigger.addEventListener('click', (e) => {
                e.stopPropagation();
                wrappers.forEach(w => { if(w !== wrapper) w.classList.remove('open'); });
                wrapper.classList.toggle('open');
            });

            options.forEach(opt => {
                opt.addEventListener('click', () => {
                    const val = opt.dataset.value;
                    const label = opt.textContent.trim();
                    
                    trigger.textContent = label;
                    hiddenInput.value = val;
                    
                    options.forEach(o => o.classList.remove('selected'));
                    opt.classList.add('selected');
                    
                    wrapper.classList.remove('open');
                    updatePrice();
                });
            });
        });

        // Close dropdowns on outside click — only remove 'open', don't stop propagation
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.custom-select-wrapper')) {
                wrappers.forEach(w => w.classList.remove('open'));
            }
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        initCustomSelects();
    });

    async function startRollyPay() {
        const btn = document.getElementById('btn-pay-trigger');
        const originalText = btn.innerHTML;
        btn.innerHTML = '⏳ Создаем платеж...';
        btn.disabled = true;

        try {
            const userBalance = currentUser ? currentUser.balance : 0;
            let amount = currentPrice;
            
            // Если баланса не хватает - пополняем на разницу
            if (userBalance < currentPrice) {
                amount = currentPrice - userBalance;
            }

            const response = await apiFetch(`${API_BASE_URL}/payment/create?amount=${amount}`, {
                method: 'POST'
            });

            if (!response.ok) throw new Error('Ошибка сервера при создании платежа');
            
            const data = await response.json();
            if (data.payment_url) {
                window.location.href = data.payment_url;
            } else {
                throw new Error('Не удалось получить ссылку на оплату');
            }
        } catch (e) {
            console.error("Payment error:", e);
            alert('Ошибка: ' + e.message);
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    }

    async function topUpBalance() {
        const amountInput = document.getElementById('topup-amount');
        const amount = parseFloat(amountInput.value);
        if (!amount || amount < 100) return alert('Минимальная сумма — 100 руб.');

        const btn = document.getElementById('btn-topup-submit');
        btn.innerHTML = '⏳ Перенаправляем...';
        btn.disabled = true;

        try {
            const response = await apiFetch(`${API_BASE_URL}/payment/create?amount=${amount}`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${authToken}` }
            });

            if (!response.ok) throw new Error('Ошибка создания платежа');
            
            const data = await response.json();
            window.location.href = data.payment_url;
        } catch (e) {
            alert('Ошибка: ' + e.message);
            btn.innerHTML = 'Пополнить';
            btn.disabled = false;
        }
    }

    function showTopUpModal() { document.getElementById('topup-modal').style.display = 'flex'; }
    function closeTopUpModal() { document.getElementById('topup-modal').style.display = 'none'; }

    // === ОФОРМЛЕНИЕ ===
    function switchFormatTab(tab) {
        const btns = document.querySelectorAll('.tab-btn');
        btns.forEach(b => b.classList.remove('active'));
        if (tab === 'upload') {
            btns[0].classList.add('active');
            document.getElementById('format-upload').style.display = 'block';
            document.getElementById('format-manual').style.display = 'none';
        } else {
            btns[1].classList.add('active');
            document.getElementById('format-upload').style.display = 'none';
            document.getElementById('format-manual').style.display = 'block';
        }
    }

    function handleFile(file) {
        if (!file) return;
        const el = document.getElementById('file-name');
        el.textContent = '📎 ' + file.name + ' ✓ загружен';
        el.style.color = 'var(--success)';
    }

    // === МАТЕРИАЛЫ: РЕДАКТОР ===
    function showEditorTab(tab) {
        const tabO = document.getElementById('ed-tab-outline');
        const tabS = document.getElementById('ed-tab-sources');
        if (tab === 'outline') {
            tabO.style.background = 'rgba(255,123,0,0.08)';
            tabO.style.borderColor = 'rgba(255,123,0,0.2)';
            tabS.style.background = 'rgba(0,0,0,0.2)';
            tabS.style.borderColor = 'var(--glass-border)';
            document.getElementById('editor-outline').style.display = 'block';
            document.getElementById('editor-sources').style.display = 'none';
            renderChaptersEditor();
        } else {
            tabS.style.background = 'rgba(255,123,0,0.08)';
            tabS.style.borderColor = 'rgba(255,123,0,0.2)';
            tabO.style.background = 'rgba(0,0,0,0.2)';
            tabO.style.borderColor = 'var(--glass-border)';
            document.getElementById('editor-outline').style.display = 'none';
            document.getElementById('editor-sources').style.display = 'block';
            renderSourcesEditor();
        }
    }

    function renderChaptersEditor() {
        if (!draftData || !draftData.outline) return;
        const container = document.getElementById('chapters-list');
        const isLocked = !isUserAuthenticated();
        
        let html = '';
        draftData.outline.chapters.forEach((ch, ci) => {
            const isFirst = ci === 0;
            const blockLocked = isLocked && !isFirst;
            
            // Если это первая заблокированная глава — открываем контейнер с блюром и плашкой
            if (isLocked && ci === 1) {
                html += `<div class="editor-container-locked">`;
                html += renderLockOverlay();
                html += `<div class="content-blur">`;
            }

            html += `<div class="editor-block">
                <div class="block-header">
                    <span class="block-num">Глава ${ch.number}</span>
                    ${blockLocked ? '' : `<button class="del-btn" onclick="removeChapter(${ci})">🗑</button>`}
                </div>
                <input type="text" class="form-control" value="${ch.title}" ${blockLocked ? 'disabled' : ''}
                       onchange="draftData.outline.chapters[${ci}].title=this.value" style="margin-bottom:10px; font-weight:600;">`;
            
            (ch.subsections || []).forEach((sub, si) => {
                html += `<div class="sub-block">
                    <span class="sub-num">${sub.number}</span>
                    <input type="text" class="form-control" value="${sub.title}" ${blockLocked ? 'disabled' : ''}
                           onchange="draftData.outline.chapters[${ci}].subsections[${si}].title=this.value" style="margin-bottom:0; flex:1;">
                    ${blockLocked ? '' : `<button class="del-btn" onclick="removeSubsection(${ci},${si})">🗑</button>`}
                </div>`;
            });

            html += `<div style="margin-left:20px; margin-top:15px; margin-bottom:10px;">
                        <label style="font-size:12px; color:var(--text-muted); display:block; margin-bottom:5px;">Индивидуальные требования для Главы ${ch.number}:</label>
                        <textarea class="form-control" placeholder="Например: Сфокусируйся на методологии..." ${blockLocked ? 'disabled' : ''}
  style="min-height:60px; font-size:13px; margin-bottom:0;"
  onchange="draftData.chapter_prompts['${ch.number}'] = this.value">${draftData.chapter_prompts[ch.number] || ''}</textarea>
                    </div>`;

            if (!blockLocked) {
                html += `<button class="add-btn" onclick="addSubsection(${ci})" style="margin-left:20px;margin-top:5px;">+ Подраздел</button>`;
            }
            html += `</div>`;
        });
        
        if (isLocked && draftData.outline.chapters.length > 1) {
            html += `</div></div>`; // Закрываем content-blur и editor-container-locked
        } else if (isLocked && draftData.outline.chapters.length === 1) {
            // Если глава всего одна - все равно показываем плашку ниже
            html += `<div class="editor-container-locked" style="min-height: 200px;">${renderLockOverlay()}</div>`;
        }
        
        container.innerHTML = html;
    }

    function renderLockOverlay() {
        return `
            <div class="lock-overlay">
                <div class="lock-plate">
                    <div style="font-size: 40px; margin-bottom: 15px;">🔒</div>
                    <h3>Контент защищен</h3>
                    <p>Чтобы увидеть полный план работы и иметь возможность редактировать его, пожалуйста, войдите в свой аккаунт.</p>
                    <button class="btn-primary" style="width: 100%; border-radius: 12px; padding: 15px;" onclick="showAuthModal()">
                        ✨ Войти через Google
                    </button>
                </div>
            </div>
        `;
    }

    function renderSourcesEditor() {
        const container = document.getElementById('sources-list');
        if (!container) return;
        const isLocked = !isUserAuthenticated();

        if (!draftData || !draftData.sources || draftData.sources.length === 0) {
            container.innerHTML = '<div style="text-align:center; padding:30px; color:var(--text-muted); background:rgba(0,0,0,0.1); border-radius:12px; border:1px dashed var(--glass-border);">Список источников пуст или еще не сгенерирован.</div>';
            return;
        }
        
        let html = isLocked ? '<div class="editor-container-locked">' : '';
        if (isLocked) html += renderLockOverlay();
        html += `<div class="${isLocked ? 'content-blur' : ''}">`;

        draftData.sources.forEach((src, i) => {
            if (!src) return;
            const url = src.url || '';
            const hasLink = typeof url === 'string' && url.length > 5 && url.startsWith('http');
            
            html += `<div class="editor-block">
                <div class="block-header">
                    <span class="block-num">Источник №${src.number || (i+1)}</span>
                    ${isLocked ? '' : `<button class="del-btn" onclick="removeSource(${i})" title="Удалить">🗑</button>`}
                </div>
                <textarea class="form-control" rows="2" style="margin-bottom:0;line-height:1.4; font-size:13px;" 
                          placeholder="Полная библиографическая запись..." ${isLocked ? 'disabled' : ''}
                          onchange="draftData.sources[${i}].citation=this.value; draftData.sources[${i}].title=this.value;">${src.citation || src.title || ''}</textarea>
                <div class="source-url-row">
                    <input type="text" class="form-control source-url-input" 
                           value="${url}" ${isLocked ? 'disabled' : ''}
                           placeholder="URL (необязательно)" 
                           onchange="draftData.sources[${i}].url=this.value; renderSourcesEditor();">
                    <a href="${hasLink ? url : '#'}" target="_blank" 
                       class="visit-link-btn ${hasLink ? '' : 'disabled'}" 
                       title="${hasLink ? 'Открыть' : 'Нет ссылки'}">🌐</a>
                </div>
            </div>`;
        });

        if (!isLocked) {
            html += `<button class="add-btn" onclick="addSource()" style="margin-top: 15px; width: 100%; justify-content: center; padding: 12px; border-style: solid; background: rgba(255,255,255,0.03);">
                    ➕ Добавить новый источник
                </button>`;
        }

        html += `</div>`; // Close content-blur
        if (isLocked) html += `</div>`; // Close editor-container-locked
        
        container.innerHTML = html;
    }

    function addChapter() {
        if (!draftData) return;
        const num = draftData.outline.chapters.length + 1;
        draftData.outline.chapters.push({
            number: String(num),
            title: 'Новая глава',
            description: '',
            subsections: [{ number: num + '.1', title: 'Новый подраздел', description: '' }]
        });
        renderChaptersEditor();
    }

    function removeChapter(ci) {
        draftData.outline.chapters.splice(ci, 1);
        // Перенумеруем
        draftData.outline.chapters.forEach((ch, i) => {
            ch.number = String(i + 1);
            (ch.subsections || []).forEach((sub, j) => {
                sub.number = (i + 1) + '.' + (j + 1);
            });
        });
        renderChaptersEditor();
    }

    function addSubsection(ci) {
        const ch = draftData.outline.chapters[ci];
        const num = (ch.subsections || []).length + 1;
        if (!ch.subsections) ch.subsections = [];
        ch.subsections.push({ number: ch.number + '.' + num, title: 'Новый подраздел', description: '' });
        renderChaptersEditor();
    }

    function removeSubsection(ci, si) {
        draftData.outline.chapters[ci].subsections.splice(si, 1);
        // Перенумеруем
        draftData.outline.chapters[ci].subsections.forEach((sub, j) => {
            sub.number = draftData.outline.chapters[ci].number + '.' + (j + 1);
        });
        renderChaptersEditor();
    }

    function addSource() {
        if (!draftData) return;
        const num = draftData.sources.length + 1;
        draftData.sources.push({ number: num, citation: '', url: '', title: '', type: 'book' });
        renderSourcesEditor();
        
        // Плавный скролл к новому элементу
        setTimeout(() => {
            const list = document.getElementById('sources-list');
            if (list.lastElementChild) {
                list.lastElementChild.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }, 100);
    }

    function removeSource(i) {
        draftData.sources.splice(i, 1);
        draftData.sources.forEach((s, idx) => s.number = idx + 1);
        renderSourcesEditor();
    }

    // === ОТПРАВКА ЗАКАЗА ===
    async function submitOrder() {
        const btn = document.getElementById('btn-submit');
        
        // Сразу переключаем UI на загрузку, чтобы не было "зависания"
        document.getElementById('form-view').style.display = 'none';
        document.getElementById('status-view').style.display = 'flex';
        renderLoadingState("Создание заказа...");
        window.scrollTo({ top: 0, behavior: 'smooth' });

        const getVal = (id) => {
            const el = document.getElementById(id);
            return el ? el.value : null;
        };

        const payload = {
            work_type: getVal('f-work_type'),
            subject: getVal('f-subject'),
            topic: getVal('f-topic'),
            pages_count: volumeType === 'pages' ? (parseInt(getVal('f-volume')) || 35) : 35,
            target_words: volumeType === 'words' ? (parseInt(getVal('f-volume')) || 0) : 0,
            figures_count: parseInt(getVal('f-images')) || 0,
            tables_count: parseInt(getVal('f-tables')) || 0,
            custom_outline: getVal('f-custom_outline') || '',
            custom_sources: getVal('f-custom_sources') || '',
            university: getVal('f-university') || '',
            student_name: getVal('f-student_name') || '',
            student_group: getVal('f-student_group') || '',
            teacher_name: getVal('f-teacher_name') || '',
            teacher_title: getVal('f-teacher_title') || ''
        };

        const headers = { 'Content-Type': 'application/json' };
        if (authToken) headers['Authorization'] = `Bearer ${authToken}`;

        try {
            const orderRes = await apiFetch(`${API_BASE_URL}/orders/`, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(payload)
            });
            
            if (!orderRes.ok) {
                const err = await orderRes.json();
                throw new Error(err.detail || 'Ошибка создания заказа');
            }
            
            const order = await orderRes.json();
            console.log("Order created:", order);
            localStorage.setItem('activeOrderId', order.id);

            await apiFetch(`${API_BASE_URL}/generation/${order.id}/start`, { 
                method: 'POST',
                headers: headers
            });
            
            startPolling(order.id);
        } catch (e) {
            console.error("Submit order error:", e);
            alert('Ошибка создания заказа. Проверьте правильность введенных данных: ' + e.message);
            // Если ошибка — возвращаем форму
            document.getElementById('form-view').style.display = 'block';
            document.getElementById('status-view').style.display = 'none';
            if (btn) {
                btn.innerHTML = '🚀 Начать генерацию';
                btn.disabled = false;
            }
        }
    }

    // === POLLING ===
    function startPolling(id) {
        document.getElementById('form-view').style.display = 'none';
        document.getElementById('status-view').style.display = 'flex';
        currentOrderId = id;
        renderLoadingState("Инициализация...");
        window.scrollTo({ top: 0, behavior: 'smooth' });
        
        const poll = async () => {
            try {
                const res = await apiFetch(`${API_BASE_URL}/orders/${id}`);
                if (!res.ok) throw new Error('Заказ не найден');
                const data = await res.json();
                
                if (data.status === 'completed') {
                    clearInterval(pollInterval);
                    localStorage.removeItem('activeOrderId');
                    renderCompleted(data);
                } else if (data.status === 'failed') {
                    clearInterval(pollInterval);
                    localStorage.removeItem('activeOrderId');
                    renderFailed(data);
                } else if (data.status === 'draft_ready') {
                    clearInterval(pollInterval);
                    renderDraftReady(data);
                } else {
                    renderProgress(data);
                }
            } catch (e) {
                console.error("Polling error:", e);
            }
        };
        poll();
        pollInterval = setInterval(poll, 5000);
    }

    // === РЕНДЕР СОСТОЯНИЙ ===
    function renderLoadingState(text) {
        document.getElementById('status-content').innerHTML = `
            <div style="display: flex; justify-content: center; align-items: center; min-height: 400px; padding: 20px;">
                <div class="glass-card" style="text-align: center; padding: 60px 40px; width: 100%; max-width: 600px; display: flex; flex-direction: column; align-items: center;">
                    <div class="neural-loader">
                        <div class="neural-ring"></div>
                        <div class="neural-ring"></div>
                        <div class="neural-ring"></div>
                        <div class="neural-orbit"><div class="neural-dot"></div></div>
                        <div class="neural-core"></div>
                    </div>
                    <h2 style="font-size: 28px; margin-bottom: 12px;" class="gradient-text">${text}</h2>
                    <p style="color: var(--text-muted); font-size: 15px;">Пожалуйста, не закрывайте вкладку.</p>
                </div>
            </div>
        `;
    }

    function renderProgress(data) {
        const isFinalGen = data.status === 'processing' || data.status.startsWith('generating');
        const statuses = isFinalGen ? [
            "Инициализация нейронных связей...",
            "Анализ научной литературы (более 1000 источников)...",
            "Формирование глубоких тезисов...",
            "Синтез уникального текста глав...",
            "Оптимизация научной лексики...",
            "Проверка логической целостности...",
            "Оформление цитат и сносок по ГОСТу...",
            "Многоэтапная проверка на плагиат...",
            "Финальная полировка стиля..."
        ] : [
            "Анализируем предметную область...",
            "Сканируем актуальные базы данных...",
            "Подбираем оптимальную структуру работы...",
            "Формируем логическое оглавление...",
            "Верифицируем список литературы...",
            "Подготавливаем черновик к утверждению..."
        ];
        
        const statusIdx = Math.floor((Date.now() / 4000) % statuses.length);
        const progressValue = data.progress || (isFinalGen ? 45 : 15);
        
        document.getElementById('status-content').innerHTML = `
            <div class="glass-card" style="text-align: center; padding: 60px 40px;">
                <div class="neural-loader">
                    <div class="neural-ring"></div>
                    <div class="neural-ring"></div>
                    <div class="neural-ring"></div>
                    <div class="neural-orbit"><div class="neural-dot"></div></div>
                    <div class="neural-core"></div>
                </div>
                
                <div style="margin: 40px 0 20px;">
                    <h2 style="font-size: 32px; margin-bottom: 12px;" class="gradient-text">
                        ${isFinalGen ? 'Пишем вашу работу...' : 'Проектируем работу...'}
                    </h2>
                    <div style="margin-bottom: 20px; display: inline-block; padding: 6px 16px; background: rgba(249, 115, 22, 0.1); border: 1px solid rgba(249, 115, 22, 0.2); border-radius: 20px; color: #f97316; font-size: 13px; font-weight: 600;">
                        🕒 Ориентировочное время ожидания: 10-20 минут
                    </div>
                    <div style="display: flex; align-items: center; justify-content: center; gap: 10px; color: var(--accent); font-weight: 600; font-size: 14px; text-transform: uppercase; letter-spacing: 2px;">
                        <span class="pulse-dot" style="background: var(--accent);"></span>
                        ${data.status_label || (isFinalGen ? 'Генерация разделов' : 'Анализ структуры')}
                    </div>
                </div>

                <div style="width: 100%; height: 4px; background: rgba(255,255,255,0.05); border-radius: 10px; margin-bottom: 30px; position: relative; overflow: hidden;">
                    <div style="position: absolute; left: 0; top: 0; height: 100%; width: ${progressValue}%; background: var(--accent); box-shadow: 0 0 15px var(--accent-glow); transition: width 2s ease-in-out;"></div>
                </div>

                <p style="color: #fff; font-weight: 500; margin-bottom: 20px; min-height: 24px; font-size: 16px;">${statuses[statusIdx]}</p>
            </div>
        `;
    }

    function renderCompleted(data) {
        const downloadUrl = data.download_url;
        document.getElementById('status-content').innerHTML = `
            <div class="glass-card" style="text-align: center; padding: 80px 20px; border-color: rgba(22, 160, 133, 0.5); background: linear-gradient(180deg, rgba(22, 160, 133, 0.05) 0%, rgba(255,255,255,0.02) 100%);">
                <div class="success-icon-animated">
                    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M20 6L9 17L4 12" style="stroke-dasharray: 20; stroke-dashoffset: 20; animation: draw-check 0.5s forwards ease-out;"></path>
                    </svg>
                </div>
                <h1 style="margin-bottom: 15px; font-size: 36px;">Работа готова!</h1>
                <p style="color: var(--text-muted); margin-bottom: 40px; font-size: 16px;">Документ успешно сформирован и доступен для скачивания.</p>
                
                <div style="display: flex; flex-direction: column; gap: 15px; align-items: center;">
                    <a href="${downloadUrl}" class="btn-primary" style="padding: 18px 40px; font-size: 18px; border-radius: 40px; width: 280px;" download>📥 Скачать .docx</a>
                    
                    <button class="btn-secondary" onclick="shareWithFriends()" style="padding: 12px 30px; border-radius: 30px; width: 280px; display: flex; align-items: center; justify-content: center; gap: 10px;">
                        <span>🚀 Посоветовать сокурсникам</span>
                    </button>
                </div>
            </div>
        `;
    }

    async function shareWithFriends() {
        if (navigator.share) {
            try {
                await navigator.share({
                    title: 'Calamo — ИИ для студентов',
                    text: 'Сделал курсовую за 15 минут через Calamo. Рекомендую!',
                    url: 'https://calamo.lol'
                });
            } catch (err) {
                console.log('Share failed');
            }
        } else {
            // Fallback: Copy to clipboard
            const dummy = document.createElement('input');
            document.body.appendChild(dummy);
            dummy.value = 'https://calamo.lol';
            dummy.select();
            document.execCommand('copy');
            document.body.removeChild(dummy);
            alert('Ссылка скопирована! Отправь её друзьям в мессенджеры.');
        }
    }

    function renderFailed(data) {
        document.getElementById('status-content').innerHTML = `
            <div class="glass-card" style="text-align: center; padding: 60px 20px; border-color: rgba(231, 76, 60, 0.5);">
                <div style="font-size: 60px; margin-bottom: 20px;">❌</div>
                <h2 style="color: var(--danger); margin-bottom: 15px;">Ошибка генерации</h2>
                <p style="color: var(--text-muted); margin-bottom: 30px;">${data.error_message || 'Произошла непредвиденная ошибка.'}</p>
                
                <div style="display: flex; flex-direction: column; gap: 12px; align-items: center;">
                    <button class="btn-primary" onclick="retryGeneration()" style="padding: 14px 30px; width: 250px;">🔄 Попробовать еще раз</button>
                    <button class="btn-secondary" onclick="toggleSupport()" style="padding: 12px 30px; width: 250px;">💬 Связаться с поддержкой</button>
                </div>
            </div>
        `;
    }

    function retryGeneration() {
        if (currentOrderId) {
            startPolling(currentOrderId);
        } else {
            openForm();
        }
    }

    // Когда черновик (план + источники) готов — загружаем их в Step-3 редактор
    function renderDraftReady(data) {
        draftData = { 
            outline: data.draft_outline, 
            sources: data.draft_sources || [],
            chapter_prompts: {} 
        };
        isDraftMode = true; // Включаем режим редактирования черновика
        
        // Переключаемся обратно в визард на шаг 3 (Материалы)
        document.getElementById('status-view').style.display = 'none';
        document.getElementById('form-view').style.display = 'block';
        currentStep = 3;
        updateWizard();

        // Показываем редактор вместо инпутов
        const matInput = document.getElementById('materials-input');
        if (matInput) matInput.style.display = 'none';
        const matEditor = document.getElementById('materials-editor');
        if (matEditor) matEditor.style.display = 'block';

        renderChaptersEditor();
        renderSourcesEditor();
    }

    function confirmDraft() {
        if (!isUserAuthenticated()) {
            showAuthModal();
            return;
        }
        // Просто переходим к оплате (Step 4)
        currentStep = 4;
        isDraftMode = false; // Выходим из режима редактора, чтобы показать кнопки навигации
        updateWizard();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    async function finalConfirm() {
        const btn = document.getElementById('btn-submit');
        btn.innerHTML = '⏳ Запускаем процесс...';
        btn.disabled = true;

        try {
            const res = await apiFetch(`${API_BASE_URL}/generation/${currentOrderId}/confirm`, {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({
                    outline: draftData.outline,
                    sources: draftData.sources,
                    chapter_prompts: draftData.chapter_prompts
                })
            });

            if (!res.ok) {
                const err = await res.json();
                let msg = 'Ошибка: ';
                if (res.status === 422 && err.detail) {
                    msg += err.detail.map(d => `${d.loc.join('.')}: ${d.msg}`).join('; ');
                } else {
                    msg += err.detail || 'Неизвестная ошибка сервера';
                }
                throw new Error(msg);
            }

            console.log("Draft confirmed, starting polling...");
            isDraftMode = false; // Выходим из режима черновика
            renderLoadingState("Подготовка финальной версии...");
            document.getElementById('form-view').style.display = 'none';
            document.getElementById('status-view').style.display = 'flex';
            window.scrollTo({ top: 0, behavior: 'smooth' });
            startPolling(currentOrderId);
        } catch (e) {
            console.error("Confirm draft error:", e);
            alert('Ошибка: ' + e.message);
            btn.innerHTML = 'Утвердить и начать написание';
            btn.disabled = false;
        }
    }

    // === COOKIE CONSENT ===
    function acceptCookies() {
        localStorage.setItem('cookies_accepted', 'true');
        document.getElementById('cookie-banner').style.display = 'none';
    }

    if (!localStorage.getItem('cookies_accepted')) {
        document.getElementById('cookie-banner').style.display = 'flex';
    }

    // === SUPPORT WIDGET LOGIC ===
    function toggleSupport() {
        const card = document.getElementById('support-card');
        const fab = document.getElementById('support-fab');
        const isActive = card.classList.contains('active');
        
        if (isActive) {
            card.classList.remove('active');
            fab.innerHTML = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`;
            fab.style.background = 'linear-gradient(135deg, var(--accent-glow), #d45500)';
        } else {
            card.classList.add('active');
            fab.innerHTML = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`;
            fab.style.background = 'rgba(255,255,255,0.1)';
            fab.style.borderColor = 'rgba(255,255,255,0.2)';
        }
    }

    async function sendSupportEmail() {
        const email = document.getElementById('sup-email').value;
        const message = document.getElementById('sup-message').value;
        const btn = document.getElementById('btn-sup-submit');

        if (!email || !message) return alert('Заполните почту и сообщение');
        
        // Валидация email
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(email)) return alert('Введите корректный email');

        btn.disabled = true;
        btn.innerHTML = '⌛ Отправка...';

        const formData = new FormData();
        formData.append('user_email', email);
        formData.append('message', message);
        const fileInput = document.getElementById('sup-files');
        if (fileInput.files.length > 0) {
            for (let i = 0; i < fileInput.files.length; i++) {
                formData.append('files', fileInput.files[i]);
            }
        }

        try {
            const res = await apiFetch(`${API_BASE_URL}/support/tickets`, {
                method: 'POST',
                body: formData
            });
            
            if (res.ok) {
                alert('Ваша заявка принята! Ответ придет на почту.');
                document.getElementById('sup-email').value = '';
                document.getElementById('sup-message').value = '';
                toggleSupport();
            } else {
                const err = await res.json();
                alert('Ошибка: ' + (err.detail || 'Не удалось отправить'));
            }
        } catch (e) {
            console.error(e);
            alert('Произошла ошибка при отправке.');
        } finally {
            btn.disabled = false;
            btn.innerHTML = 'Отправить заявку';
        }
    }

    // === AUTH LOGIC ===
    async function handleLogin(provider) {
        if (provider === 'google') {
            try {
                const res = await apiFetch(`${API_BASE_URL}/auth/google/url`);
                const data = await res.json();
                window.location.href = data.url; // Перенаправляем в Google
            } catch (e) {
                alert('Ошибка при получении ссылки на авторизацию');
            }
            return;
        }
        
        // Заглушка для Apple ID пока не настроен
        alert('Сенсорный вход через Apple ID временно недоступен. Используйте Google.');
    }

    // Проверка токена в URL после редиректа от Google
    (function checkUrlToken() {
        // Больше не используется, так как токен ставится через HttpOnly куки
        const hash = window.location.hash;
        if (hash && hash.includes('token=')) {
            // Очищаем URL
            window.history.replaceState(null, null, window.location.pathname + window.location.search);
        }
    })();

    async function fetchProfile() {
        try {
            const res = await apiFetch(`${API_BASE_URL}/auth/me`);
            if (!res.ok) {
                throw new Error('Not authorized');
            }
            currentUser = await res.json();
            updateUserUI();
        } catch (e) {
            console.error('Session expired or error:', e);
            // Если мы думали что залогинены, но сервер сказал нет - чистим куку
            if (getCookie('logged_in_status')) {
                document.cookie = 'logged_in_status=; Max-Age=0; path=/';
            }
        }
    }

    async function fetchMyWorks() {
        console.log("fetchMyWorks() started");
        if (getCookie('logged_in_status') !== 'true') {
            console.log("fetchMyWorks aborted: not logged in");
            return;
        }
        try {
            const res = await apiFetch(`${API_BASE_URL}/orders/user/me`);
            const works = await res.json();
            renderWorksList(works);
        } catch (e) {
            console.error('Error fetching works:', e);
        }
    }

    async function resumeOrder(id) {
        currentOrderId = id;
        localStorage.setItem('activeOrderId', id);
        
        // Если мы на главном или в профиле - переходим в режим ожидания
        document.getElementById('landing-view').style.display = 'none';
        document.getElementById('profile-view').style.display = 'none';
        document.getElementById('status-view').style.display = 'block';
        renderLoadingState("Восстановление сессии...");
        
        // Запускаем поллинг, он сам разберется куда нас кинуть (в прогресс или в редактор плана)
        startPolling(id);
    }

    async function deleteWork(id) {
        if (!confirm('Вы уверены, что хотите удалить эту работу? Это действие необратимо.')) return;
        
        try {
            const res = await apiFetch(`${API_BASE_URL}/orders/${id}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${authToken}` }
            });
            if (res.ok) {
                fetchMyWorks(); // Обновляем список
            } else {
                const err = await res.json();
                alert('Ошибка: ' + (err.detail || 'Не удалось удалить работу'));
            }
        } catch (e) {
            console.error('Delete error:', e);
            alert('Произошла ошибка при соединении с сервером');
        }
    }

    function renderWorksList(works) {
        const container = document.getElementById('works-list');
        if (works.length === 0) {
            container.innerHTML = '<p style="color: var(--text-muted)">У вас пока нет созданных работ.</p>';
            return;
        }

        container.innerHTML = works.map(w => {
            const date = new Date(w.created_at).toLocaleDateString('ru-RU');
            
            let badgeHTML = '';
            if (w.status === 'completed') {
                badgeHTML = `<span class="status-badge completed"><span class="status-dot"></span> Готова</span>`;
            } else if (w.status === 'failed') {
                badgeHTML = `<span class="status-badge failed"><span class="status-dot"></span> Ошибка</span>`;
            } else {
                badgeHTML = `<span class="status-badge pending"><span class="status-dot pulse-dot"></span> В работе</span>`;
            }

            return `
                <div class="work-card">
                    <div class="work-info">
                        <h4 style="font-size: 15px; margin-bottom: 5px; color: #fff;">${DOMPurify.sanitize(w.topic)}</h4>
                        <p style="margin-bottom: 8px; font-size: 12px; color: var(--text-muted);">${DOMPurify.sanitize(w.work_type)} • ${date}</p>
                        ${badgeHTML}
                    </div>
                    <div class="work-actions" style="display: flex; align-items: center; gap: 12px;">
                        ${w.status === 'completed' ? `
                            <a href="${w.download_url}" class="btn-primary" style="padding: 10px 18px; font-size: 13px; border-radius: 12px; display: flex; align-items: center; gap: 8px;" download>
<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
Скачать
                            </a>` : 
                          w.status === 'draft_ready' ? `
                            <button class="btn-primary" style="padding: 10px 18px; font-size: 13px; border-radius: 12px; background: var(--accent-light);" onclick="resumeOrder('${w.id}')">
Редактировать
                            </button>` :
                          w.status !== 'failed' ? `
                            <button class="btn-secondary" style="padding: 10px 18px; font-size: 13px; border-radius: 12px;" onclick="resumeOrder('${w.id}')">
Следить
                            </button>` : ''}
                        
                        <button class="del-btn del-btn-premium" style="padding: 10px; height: 38px; width: 38px; display: flex; align-items: center; justify-content: center; border-radius: 10px;" 
onclick="deleteWork('${w.id}')" title="Удалить">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
<polyline points="3 6 5 6 21 6"></polyline>
<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
<line x1="10" y1="11" x2="10" y2="17"></line>
<line x1="14" y1="11" x2="14" y2="17"></line>
                            </svg>
                        </button>
                    </div>
                </div>
            `;
        }).join('');
    }

    function updateUserUI() {
        if (!currentUser) return;
        const headerAuth = document.getElementById('header-auth');
        headerAuth.innerHTML = `
            <div class="user-nav">
                <div class="user-balance">${currentUser.balance} ₽</div>
                <div class="user-avatar" onclick="openProfile()">
                    <img src="${currentUser.avatar_url || 'https://cdn-icons-png.flaticon.com/512/847/847969.png'}" style="width: 100%; border-radius: 50%;">
                </div>
                <button class="glass-btn-logout" onclick="logout()">Выйти</button>
            </div>
        `;
        const profileBalance = document.getElementById('profile-balance');
        if (profileBalance) profileBalance.innerText = `${currentUser.balance} ₽`;
    }

    function logout() {
        document.getElementById('logout-confirm-modal').style.display = 'flex';
    }

    function closeLogoutModal() {
        document.getElementById('logout-confirm-modal').style.display = 'none';
    }

    async function confirmLogout() {
        try {
            await apiFetch(`${API_BASE_URL}/auth/logout`, { method: 'POST' });
        } catch (e) {
            console.error("Logout error", e);
        }
        document.cookie = 'logged_in_status=; Max-Age=0; path=/';
        window.location.reload();
    }


    // Авто-логин и резюмирование сессии при загрузке
    (function initSession() {
        const isLoggedIn = getCookie('logged_in_status') === 'true';
        
        if (isLoggedIn || (authToken && authToken !== 'null')) {
            fetchProfile();
            fetchMyWorks();
        }
        
        // Если есть активный заказ, возвращаемся к нему
        if (currentOrderId && currentOrderId !== 'null') {
            resumeOrder(currentOrderId);
        } else {
            checkActiveOrder();
        }
    })();

    // === ОТЗЫВЫ ===
    async function loadReviews() {
        try {
            const res = await apiFetch(`${API_BASE_URL}/reviews/`);
            const reviews = await res.json();
            const container = document.getElementById('reviews-container');
            if (!container) return;
            
            if (reviews.length === 0) {
                container.innerHTML = '<p style="grid-column: 1/-1; text-align: center; color: var(--text-muted);">Пока нет отзывов. Будьте первым!</p>';
                return;
            }

            // Дублируем отзывы для бесконечной ленты
            if (reviews.length > 0) {
                const html = reviews.map(r => {
                    const stars = Array(5).fill(0).map((_, i) => `
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="${i < r.rating ? 'var(--accent)' : 'rgba(255,255,255,0.05)'}" stroke="none">
                            <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"></path>
                        </svg>
                    `).join('');

                    return `
                        <div class="review-card">
                            <div class="review-header">
<div class="review-avatar">${r.user_name ? DOMPurify.sanitize(r.user_name[0].toUpperCase()) : '?'}</div>
<div class="review-name">${DOMPurify.sanitize(r.user_name)}</div>
                            </div>
                            <div class="review-rating">${stars}</div>
                            <div class="review-text">"${DOMPurify.sanitize(r.text)}"</div>
                            <div class="review-date">${new Date(r.created_at).toLocaleDateString('ru-RU')}</div>
                        </div>
                    `;
                }).join('');
                
                // Повторяем контент 3 раза для бесшовности
                container.innerHTML = html + html + html;
            }
        } catch (e) {
            console.error("Load reviews error:", e);
        }
    }

    function openReviewModal() { document.getElementById('review-modal').style.display = 'flex'; }
    function closeReviewModal() { document.getElementById('review-modal').style.display = 'none'; }

    async function submitReview() {
        const name = document.getElementById('rev-name').value;
        const text = document.getElementById('rev-text').value;
        const rating = document.getElementById('rev-rating').value;

        if (!name || !text) return alert('Заполните имя и текст отзыва');

        try {
            const res = await apiFetch(`${API_BASE_URL}/reviews/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_name: name, text: text, rating: parseInt(rating) })
            });
            if (res.ok) {
                closeReviewModal();
                loadReviews();
                alert('Спасибо за отзыв!');
                document.getElementById('rev-name').value = '';
                document.getElementById('rev-text').value = '';
            }
        } catch (e) {
            alert('Ошибка при отправке отзыва');
        }
    }

    async function deleteReview(id) {
        if (!confirm('Удалить этот отзыв?')) return;
        try {
            await apiFetch(`${API_BASE_URL}/reviews/${id}`, { method: 'DELETE' });
            loadReviews();
        } catch (e) {
            alert('Ошибка удаления');
        }
    }

    // Запуск при загрузке
    loadReviews();

