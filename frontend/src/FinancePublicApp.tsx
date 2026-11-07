import { TransactionReviewView } from './features/TransactionReviewView';
import './finance-public.css';

export default function FinancePublicApp() {
  return <main className="finance-public" id="main-content">
    <header className="finance-public-heading">
      <p className="tr-eyebrow">VeriScope Finance</p>
      <h1>交易证据复核</h1>
      <p className="tr-note">请使用脱敏资料或模拟示例。分析与重开会将输入发送到服务器内存处理，不自动保存交易；下载的复核包保留在本机。</p>
    </header>
    <TransactionReviewView/>
  </main>;
}
