import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";

class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    console.error("Fatal React render error", error, info);
  }
  render() {
    if (!this.state.error) return this.props.children;
    const message = this.state.error?.message || String(this.state.error);
    return (
      <div style={{minHeight:"100vh",display:"grid",placeItems:"center",background:"#f6f4ef",fontFamily:"system-ui, sans-serif",padding:24}}>
        <div style={{maxWidth:720,width:"100%",background:"#fff",border:"1px solid #ddd7ca",borderRadius:12,padding:24,boxShadow:"0 10px 35px rgba(0,0,0,.08)"}}>
          <h2 style={{marginTop:0}}>Интерфейс не смог загрузиться</h2>
          <p>Рабочие данные не изменены. Ошибка произошла только при отображении страницы.</p>
          <pre style={{whiteSpace:"pre-wrap",background:"#f7f7f7",padding:12,borderRadius:8,overflow:"auto"}}>{message}</pre>
          <button onClick={() => window.location.reload()} style={{padding:"9px 14px",border:0,borderRadius:8,background:"#1e3a5f",color:"white",fontWeight:700,cursor:"pointer"}}>Перезагрузить страницу</button>
        </div>
      </div>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <AppErrorBoundary>
    <App />
  </AppErrorBoundary>
);
