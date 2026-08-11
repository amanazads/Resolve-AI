import { useState, useEffect, useRef, useCallback } from 'react';

export const SUPPORTED_LANGUAGES = [
  { code: 'en-US', name: 'English', flag: '🇺🇸' },
  { code: 'es-ES', name: 'Español', flag: '🇪🇸' },
  { code: 'hi-IN', name: 'हिन्दी', flag: '🇮🇳' },
  { code: 'fr-FR', name: 'Français', flag: '🇫🇷' },
  { code: 'de-DE', name: 'Deutsch', flag: '🇩🇪' },
  { code: 'zh-CN', name: '中文', flag: '🇨🇳' }
];

export function useVoice() {
  const [lang, setLang] = useState('en-US');
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [autoSpeak, setAutoSpeak] = useState(false);

  const [transcript, setTranscript] = useState('');
  const recognitionRef = useRef(null);
  const utteranceRef = useRef(null);

  const isSupported = typeof window !== 'undefined' && 
    ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window);

  useEffect(() => {
    if (!isSupported) return;

    try {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      const recognition = new SpeechRecognition();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = lang;
      recognitionRef.current = recognition;
    } catch (e) {
      console.warn('SpeechRecognition init warning:', e);
    }
  }, [lang, isSupported]);

  const stopSpeaking = useCallback(() => {
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      try {
        if (utteranceRef.current) {
          utteranceRef.current.onstart = null;
          utteranceRef.current.onend = null;
          utteranceRef.current.onerror = null;
          utteranceRef.current = null;
        }
        window.speechSynthesis.cancel();
      } catch (e) {
        console.warn('Error stopping speech synthesis:', e);
      } finally {
        setIsSpeaking(false);
      }
    }
  }, []);

  const startListening = useCallback((onFinalTranscript) => {
    if (!isSupported || !recognitionRef.current) return;

    try {
      stopSpeaking();

      if (isListening) {
        try {
          recognitionRef.current.stop();
        } catch (e) {}
      }

      setTranscript('');
      setIsListening(true);

      const recognition = recognitionRef.current;
      recognition.lang = lang;

      recognition.onresult = (event) => {
        let currentTranscript = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          currentTranscript += event.results[i][0].transcript;
        }
        setTranscript(currentTranscript);
        if (event.results[0].isFinal && onFinalTranscript) {
          onFinalTranscript(currentTranscript);
        }
      };

      recognition.onerror = (event) => {
        console.warn('Speech recognition error event:', event.error);
        setIsListening(false);
      };

      recognition.onend = () => {
        setIsListening(false);
      };

      recognition.start();
    } catch (err) {
      console.warn('Failed to start speech recognition:', err);
      setIsListening(false);
    }
  }, [lang, isSupported, isListening, stopSpeaking]);

  const stopListening = useCallback(() => {
    if (recognitionRef.current && isListening) {
      try {
        recognitionRef.current.stop();
      } catch (e) {}
      setIsListening(false);
    }
  }, [isListening]);

  const speak = useCallback((text, customLang = null, onEndCallback = null) => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;

    try {
      stopSpeaking();

      const cleanText = (text || '')
        .replace(/\[([^\]]+)\]\([^\)]+\)/g, '$1')
        .replace(/[\*\_~`#]/g, '')
        .replace(/Source:.*$/i, '')
        .trim();

      if (!cleanText) return;

      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.lang = customLang || lang || 'en-US';

      utterance.onstart = () => {
        setTimeout(() => setIsSpeaking(true), 0);
      };

      utterance.onend = () => {
        setTimeout(() => {
          setIsSpeaking(false);
          utteranceRef.current = null;
          if (onEndCallback) {
            try {
              onEndCallback();
            } catch (e) {}
          }
        }, 0);
      };

      utterance.onerror = (e) => {
        console.warn('SpeechSynthesis error event:', e);
        setTimeout(() => {
          setIsSpeaking(false);
          utteranceRef.current = null;
        }, 0);
      };

      utteranceRef.current = utterance;
      window.speechSynthesis.speak(utterance);
    } catch (e) {
      console.warn('Speech synthesis exception caught:', e);
      setIsSpeaking(false);
    }
  }, [lang, stopSpeaking]);

  return {
    lang,
    setLang,
    isListening,
    isSpeaking,
    autoSpeak,
    setAutoSpeak,
    transcript,
    isSupported,
    startListening,
    stopListening,
    speak,
    stopSpeaking
  };
}
