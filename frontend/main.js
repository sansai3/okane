// とてもシンプルなフロントエンドロジックです。
// 中学生でも読めるように、難しい書き方は避けています。

const menuSection = document.getElementById("menu");
const emailFormSection = document.getElementById("email-form");
const emailResultBox = document.getElementById("email-result");
const mailFolderInput = document.getElementById("mail-folder");
const downloadFolderInput = document.getElementById("download-folder");
const csvOutputInput = document.getElementById("csv-output");
const runEmailButton = document.getElementById("run-email");
const closeEmailButton = document.getElementById("close-email");
const title = document.getElementById("app-title");

let appConfig = null;

// ページを開いたらすぐに設定ファイルを読み込みます。
window.addEventListener("DOMContentLoaded", async () => {
  await loadConfig();
  setupEmailButtons();
});

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    appConfig = await response.json();
    const appTitle = appConfig?.app?.title || "メニュー";
    title.textContent = appTitle;

    buildMenuButtons(appConfig?.app?.buttons || []);
    setDefaultPaths(appConfig?.paths || {});
  } catch (error) {
    console.error("設定の読み込みに失敗", error);
    title.textContent = "設定を読み込めませんでした";
  }
}

function buildMenuButtons(buttons) {
  menuSection.innerHTML = "";
  buttons.forEach((button) => {
    const buttonElement = document.createElement("button");
    buttonElement.className = "menu-button";
    buttonElement.dataset.module = button.module;
    buttonElement.dataset.entryPoint = button.entry_point;
    buttonElement.dataset.buttonId = button.id;

    const heading = document.createElement("h3");
    heading.textContent = button.label;

    const description = document.createElement("p");
    description.textContent = button.description;

    buttonElement.appendChild(heading);
    buttonElement.appendChild(description);

    buttonElement.addEventListener("click", () => handleButtonClick(button));
    menuSection.appendChild(buttonElement);
  });
}

function setDefaultPaths(paths) {
  // 設定ファイルに書かれている初期値をフォームに入れておきます。
  mailFolderInput.value = paths.default_mail_folder || "";
  downloadFolderInput.value = paths.default_download_folder || "";
  csvOutputInput.value = paths.default_csv_output || "";
}

function handleButtonClick(button) {
  // module と entryPoint を使えば、どの Python を呼ぶか簡単に変えられます。
  if (button.module === "email_processor") {
    showEmailForm();
    return;
  }

  if (button.id === "settings_button") {
    openSettingsFile();
    return;
  }

  alert(`"${button.label}" はまだ準備中です。設定ファイルで module を差し替えると別の処理を呼び出せます。`);
}

function showEmailForm() {
  emailFormSection.classList.remove("hidden");
  emailResultBox.textContent = "";
}

function hideEmailForm() {
  emailFormSection.classList.add("hidden");
}

async function runEmailProcessing() {
  const payload = {
    mail_folder: mailFolderInput.value.trim(),
    download_folder: downloadFolderInput.value.trim(),
    csv_output: csvOutputInput.value.trim(),
  };

  emailResultBox.textContent = "処理中です。少しお待ちください...";

  try {
    const response = await fetch("/api/process/email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const result = await response.json();
    emailResultBox.textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    console.error(error);
    emailResultBox.textContent = "エラーが発生しました。コンソールを確認してください。";
  }
}

async function openSettingsFile() {
  try {
    const response = await fetch("/api/open-settings", { method: "POST" });
    const result = await response.json();
    alert(`設定ファイルの場所: ${result.path}`);
  } catch (error) {
    alert("設定ファイルの場所を取得できませんでした。");
  }
}

function setupEmailButtons() {
  runEmailButton.addEventListener("click", runEmailProcessing);
  closeEmailButton.addEventListener("click", hideEmailForm);
}
