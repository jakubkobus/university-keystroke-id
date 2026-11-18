// All user-facing text in one place (Polish: the participants are Polish students).
// Translate or reword here only; the logic never builds sentences itself.

export const T = {
  appName: "KeystrokeID",

  loginTitle: "Badanie dynamiki pisania",
  loginIntro: "Podaj swój osobisty kod uczestnika, który dostałeś od prowadzących badanie.",
  loginLabel: "Kod uczestnika",
  loginButton: "Dalej",
  loginInvalid: "Nieprawidłowy kod. Sprawdź, czy został wpisany bez pomyłek.",
  networkError: "Brak połączenia z serwerem. Spróbuj ponownie za chwilę.",

  // PLACEHOLDER: the final consent wording must be approved by the supervisor / university (GDPR).
  consentTitle: "Zgoda na udział w badaniu",
  consentBody: [
    "Badanie polega na wielokrotnym przepisywaniu krótkiego, jawnego tekstu testowego. " +
      "Zapisujemy wyłącznie czasy naciśnięcia i puszczenia klawiszy w polu na tej stronie " +
      "oraz podstawowe informacje o przeglądarce i systemie. Nie zapisujemy treści innych okien ani haseł.",
    "Takie dane mogą stanowić dane biometryczne. Są przechowywane pod pseudonimem " +
      "i wykorzystywane wyłącznie do projektu uczelnianego.",
    "Udział jest dobrowolny. W każdej chwili możesz zrezygnować i poprosić o usunięcie swoich danych.",
  ],
  consentCheckbox: "Przeczytałem(-am) informacje i dobrowolnie wyrażam zgodę na udział w badaniu.",
  consentButton: "Wyrażam zgodę",

  homeTitle: "Twoje sesje",
  homeHello: (id) => `Uczestnik ${id}`,
  sessionRow: (label, valid, target, invalid) =>
    `${label}: ${valid} / ${target} poprawnych wpisów` + (invalid ? ` (odrzuconych: ${invalid})` : ""),
  noSessions: "Nie masz jeszcze żadnej sesji.",
  startSession: "Rozpocznij nową sesję",
  continueSession: (label) => `Kontynuuj sesję ${label}`,
  nextSessionHint: "Kolejną sesję wykonaj w innym dniu, jeśli prowadzący nie ustalili inaczej.",
  allDone: "Dziękujemy! Wykonałeś(-aś) wszystkie sesje.",
  sessionLimit: "Osiągnięto limit sesji.",

  taskTitle: (label) => `Sesja ${label}`,
  taskInstruction: "Przepisz powyższy tekst w swoim zwykłym tempie. Nie używaj Backspace ani wklejania; po pomyłce zacznij wpis od nowa.",
  taskKeyboard: "Użyj fizycznej klawiatury komputera (nie telefonu ani klawiatury ekranowej).",
  taskPlaceholder: "Kliknij tutaj i zacznij pisać",
  taskCounter: (valid, target) => `Poprawne wpisy: ${valid} / ${target}`,
  taskOk: "Poprawnie! Przepisz tekst ponownie.",
  taskSending: "Zapisuję…",
  taskPending: (n) => `Oczekuje na wysłanie: ${n} (zostaną wysłane automatycznie).`,
  taskDone: "Sesja zakończona. Dziękujemy!",
  taskBack: "Wróć do listy sesji",
  taskExit: "Zakończ na dziś",

  reasons: {
    wrong_key: "Wpisano niewłaściwy znak. Zacznij od nowa.",
    wrong_text: "Wpisany tekst nie zgadza się ze wzorcem. Zacznij od nowa.",
    correction: "Użyto Backspace/Delete. Zacznij od nowa, bez poprawiania.",
    autorepeat: "Klawisz został przytrzymany zbyt długo (autopowtarzanie). Zacznij od nowa.",
    unsupported_key: "Użyto klawisza spoza tekstu (np. Shift, strzałki). Zacznij od nowa.",
    composition: "Wykryto metodę wprowadzania tekstu (IME/klawiatura ekranowa). Użyj zwykłej klawiatury.",
    up_without_down: "Zarejestrowano nieoczekiwane puszczenie klawisza. Zacznij od nowa.",
    too_many_keys: "Za dużo znaków. Zacznij od nowa.",
    focus_lost: "Pole straciło fokus. Zacznij od nowa.",
    paste: "Wklejanie jest zablokowane. Zacznij od nowa i wpisz tekst ręcznie.",
  },
  reasonDefault: "Wpis nieprawidłowy. Zacznij od nowa.",

  errorGeneric: "Coś poszło nie tak. Odśwież stronę i spróbuj ponownie.",
  retry: "Spróbuj ponownie",
};
