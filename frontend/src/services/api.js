import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || 'https://resolve-ai-cj60.onrender.com/api';

export const sendChatMessage = async (sessionId, userId, message) => {
  const response = await axios.post(`${API_BASE}/chat`, {
    session_id: sessionId,
    user_id: userId,
    message: message
  });
  return response.data;
};

export const fetchChatHistory = async (sessionId) => {
  const response = await axios.get(`${API_BASE}/chat/history/${sessionId}`);
  return response.data;
};

export const escalateSession = async (sessionId, userId, reason) => {
  const response = await axios.post(`${API_BASE}/escalate`, {
    session_id: sessionId,
    user_id: userId,
    reason: reason
  });
  return response.data;
};

export const checkHealth = async () => {
  const response = await axios.get(`${API_BASE}/health`);
  return response.data;
};
