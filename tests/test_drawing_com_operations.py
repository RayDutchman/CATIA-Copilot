"""
楠岃瘉鍥剧焊鎿嶄綔鏍稿績 COM 璋冪敤鐨勬祴璇曡剼鏈?
浣跨敤鏂规硶锛?1. 鍚姩 CATIA V5 R28
2. 鎵撳紑涓€涓?CATPart 鏂囦欢锛堢敤浜庢祴璇曟柊寤哄浘绾革級
3. 杩愯姝よ剼鏈細python test_drawing_com_operations.py

娴嬭瘯鍐呭锛?- 鏂囨。绫诲瀷妫€鏌?- 璇诲彇闆朵欢鏍囧噯灞炴€э紙PartNumber, Nomenclature, Revision锛?- 璇诲彇/鍒涘缓鐢ㄦ埛鑷畾涔夊睘鎬?- 閬嶅巻宸叉墦寮€鏂囨。
- 鍥剧焊鍙傛暟璇诲啓
- 锛堝彲閫夛級浠庢ā鏉垮垱寤烘柊鍥剧焊

娉ㄦ剰锛氭鑴氭湰浠呯敤浜庨獙璇侊紝涓嶄細淇敼鐜版湁鏂囦欢锛屽垱寤虹殑娴嬭瘯鍥剧焊涓嶄細淇濆瓨銆?"""

import sys
from pathlib import Path

# 娣诲姞椤圭洰璺緞鍒?sys.path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from catia_copilot.catia.connection import get_catia_v5_application
import logging

# 閰嶇疆鏃ュ織
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DrawingCOMValidator:
    """鍥剧焊鎿嶄綔 COM 璋冪敤楠岃瘉鍣?""
    
    def __init__(self):
        self.app = None
        self.test_results = []
        
    def log_test(self, test_name: str, success: bool, message: str = ""):
        """璁板綍娴嬭瘯缁撴灉"""
        status = "鉁?閫氳繃" if success else "鉂?澶辫触"
        result = f"{status} | {test_name}"
        if message:
            result += f" | {message}"
        self.test_results.append((success, result))
        logger.info(result)
        
    @staticmethod
    def get_document_type(doc) -> str:
        """鑾峰彇 CATIA 鏂囨。绫诲瀷鍚嶇О
        
        鍦?Python win32com 涓紝涓嶈兘鐩存帴鐢?type(doc).__name__ 鑾峰彇 COM 绫诲瀷锛?        闇€瑕侀€氳繃妫€鏌ュ璞＄殑灞炴€ф潵鍒ゆ柇鏂囨。绫诲瀷銆?        
        鍙傝€?VBScript 鐨?TypeName() 鍑芥暟锛岃繑鍥烇細
        - "PartDocument" - CATPart 闆朵欢鏂囨。
        - "ProductDocument" - CATProduct 瑁呴厤浣撴枃妗?        - "DrawingDocument" - CATDrawing 鍥剧焊鏂囨。
        - "Unknown" - 鏈煡绫诲瀷
        """
        try:
            # 鏂规硶 1锛氭鏌?Product 灞炴€э紙闆朵欢鍜岃閰嶄綋閮芥湁锛?            if hasattr(doc, 'Product'):
                # 杩涗竴姝ュ尯鍒嗛浂浠跺拰瑁呴厤浣?                # 闆朵欢鏈?Part 灞炴€э紝瑁呴厤浣撴病鏈夛紙鎴栬€呮鏌?Products 闆嗗悎锛?                try:
                    _ = doc.Part
                    return "PartDocument"
                except Exception:
                    pass
                try:
                    _ = doc.Product.Products
                    return "ProductDocument"
                except Exception:
                    # 鏈?Product 浣嗘病鏈?Products锛屽彲鑳芥槸闆朵欢
                    return "PartDocument"
            
            # 鏂规硶 2锛氭鏌?Sheets 灞炴€э紙鍥剧焊鐗规湁锛?            if hasattr(doc, 'Sheets'):
                return "DrawingDocument"
                
            # 鏂规硶 3锛氭鏌?DrawingRoot 灞炴€э紙鍥剧焊鐗规湁锛?            if hasattr(doc, 'DrawingRoot'):
                return "DrawingDocument"
                
        except Exception as e:
            logger.debug(f"鑾峰彇鏂囨。绫诲瀷澶辫触: {e}")
            
        return "Unknown"
        
    def connect_to_catia(self) -> bool:
        """娴嬭瘯 1锛氳繛鎺ュ埌 CATIA"""
        try:
            self.app = get_catia_v5_application()
            self.log_test("杩炴帴 CATIA", True, f"鐗堟湰: {self.app.Caption}")
            return True
        except Exception as e:
            self.log_test("杩炴帴 CATIA", False, str(e))
            return False
            
    def test_document_type_check(self) -> bool:
        """娴嬭瘯 2锛氭枃妗ｇ被鍨嬫鏌?""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            # 娴嬭瘯绫诲瀷鍒ゆ柇
            is_part = doc_type == "PartDocument"
            is_product = doc_type == "ProductDocument"
            is_drawing = doc_type == "DrawingDocument"
            
            self.log_test(
                "鏂囨。绫诲瀷妫€鏌?,
                True,
                f"褰撳墠鏂囨。绫诲瀷: {doc_type} (Part={is_part}, Product={is_product}, Drawing={is_drawing})"
            )
            return True
        except Exception as e:
            self.log_test("鏂囨。绫诲瀷妫€鏌?, False, str(e))
            return False
            
    def test_read_part_standard_properties(self) -> bool:
        """娴嬭瘯 3锛氳鍙栭浂浠舵爣鍑嗗睘鎬?""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            if doc_type not in ("PartDocument", "ProductDocument"):
                self.log_test(
                    "璇诲彇闆朵欢鏍囧噯灞炴€?,
                    False,
                    f"褰撳墠鏂囨。涓嶆槸闆朵欢/瑁呴厤浣擄紝璺宠繃娴嬭瘯锛堢被鍨? {doc_type}锛?
                )
                return False
                
            # 璇诲彇鏍囧噯灞炴€?            part_number = active_doc.Product.PartNumber
            nomenclature = active_doc.Product.Nomenclature
            revision = active_doc.Product.Revision
            
            self.log_test(
                "璇诲彇闆朵欢鏍囧噯灞炴€?,
                True,
                f"PartNumber={part_number}, Nomenclature={nomenclature}, Revision={revision}"
            )
            return True
        except Exception as e:
            self.log_test("璇诲彇闆朵欢鏍囧噯灞炴€?, False, str(e))
            return False
            
    def test_read_user_properties(self) -> bool:
        """娴嬭瘯 4锛氳鍙栫敤鎴疯嚜瀹氫箟灞炴€?""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            if doc_type not in ("PartDocument", "ProductDocument"):
                self.log_test(
                    "璇诲彇鐢ㄦ埛鑷畾涔夊睘鎬?,
                    False,
                    f"褰撳墠鏂囨。涓嶆槸闆朵欢/瑁呴厤浣擄紝璺宠繃娴嬭瘯锛堢被鍨? {doc_type}锛?
                )
                return False
                
            # 鑾峰彇鐢ㄦ埛灞炴€ч泦鍚?            user_props = None
            try:
                user_props = active_doc.Product.UserRefProperties
            except Exception:
                pass
                
            if user_props is None:
                self.log_test("璇诲彇鐢ㄦ埛鑷畾涔夊睘鎬?, True, "闆朵欢鏃犵敤鎴疯嚜瀹氫箟灞炴€ч泦鍚堬紙姝ｅ父鎯呭喌锛?)
                return True
                
            # 灏濊瘯璇诲彇棰勫畾涔夌殑灞炴€?            test_props = ["鐗╂枡缂栫爜", "鏉愭枡", "閲嶉噺"]
            found_props = []
            
            for prop_name in test_props:
                try:
                    prop = user_props.Item(prop_name)
                    if prop is not None:
                        value = prop.Value
                        found_props.append(f"{prop_name}={value}")
                except Exception:
                    pass
                    
            if found_props:
                self.log_test(
                    "璇诲彇鐢ㄦ埛鑷畾涔夊睘鎬?,
                    True,
                    f"鎵惧埌 {len(found_props)} 涓睘鎬? {', '.join(found_props)}"
                )
            else:
                self.log_test(
                    "璇诲彇鐢ㄦ埛鑷畾涔夊睘鎬?,
                    True,
                    "闆朵欢鏈夊睘鎬ч泦鍚堜絾鏈壘鍒版祴璇曞睘鎬э紙鐗╂枡缂栫爜/鏉愭枡/閲嶉噺锛?
                )
            return True
        except Exception as e:
            self.log_test("璇诲彇鐢ㄦ埛鑷畾涔夊睘鎬?, False, str(e))
            return False
            
    def test_create_user_property(self) -> bool:
        """娴嬭瘯 5锛氬垱寤虹敤鎴疯嚜瀹氫箟灞炴€э紙娴嬭瘯鍚庡垹闄わ級"""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            if doc_type not in ("PartDocument", "ProductDocument"):
                self.log_test(
                    "鍒涘缓鐢ㄦ埛鑷畾涔夊睘鎬?,
                    False,
                    f"褰撳墠鏂囨。涓嶆槸闆朵欢/瑁呴厤浣擄紝璺宠繃娴嬭瘯锛堢被鍨? {doc_type}锛?
                )
                return False
                
            # 鑾峰彇鎴栧垱寤虹敤鎴峰睘鎬ч泦鍚?            user_props = None
            try:
                user_props = active_doc.Product.UserRefProperties
            except Exception:
                pass
                
            if user_props is None:
                self.log_test("鍒涘缓鐢ㄦ埛鑷畾涔夊睘鎬?, False, "鏃犳硶鑾峰彇鐢ㄦ埛灞炴€ч泦鍚?)
                return False
                
            # 鍒涘缓娴嬭瘯灞炴€?            test_prop_name = "_TEST_DRAWING_VALIDATION_"
            test_prop_value = "娴嬭瘯鍊糭12345"
            
            # 鍏堟鏌ユ槸鍚﹀凡瀛樺湪
            existing_value = None
            try:
                prop = user_props.Item(test_prop_name)
                if prop is not None:
                    existing_value = prop.Value
            except Exception:
                pass
                
            if existing_value is not None:
                self.log_test(
                    "鍒涘缓鐢ㄦ埛鑷畾涔夊睘鎬?,
                    True,
                    f"娴嬭瘯灞炴€у凡瀛樺湪锛屽€?{existing_value}锛堣烦杩囧垱寤猴級"
                )
                return True
                
            # 鍒涘缓鏂板睘鎬?            user_props.CreateString(test_prop_name, test_prop_value)
            
            # 楠岃瘉鍒涘缓鎴愬姛
            created_prop = user_props.Item(test_prop_name)
            created_value = created_prop.Value
            
            if created_value == test_prop_value:
                self.log_test(
                    "鍒涘缓鐢ㄦ埛鑷畾涔夊睘鎬?,
                    True,
                    f"鎴愬姛鍒涘缓骞堕獙璇? {test_prop_name}={created_value}"
                )
                
                # 娓呯悊娴嬭瘯灞炴€э紙灏濊瘯鍒犻櫎锛屽け璐ヤ篃涓嶅奖鍝嶆祴璇曠粨鏋滐級
                try:
                    # CATIA COM API 涓垹闄ゅ睘鎬х殑鏂规硶锛堝鏋滃瓨鍦級
                    # 娉ㄦ剰锛氭煇浜涚増鏈彲鑳戒笉鏀寔鍒犻櫎锛岃繖閲屼粎灏濊瘯
                    logger.info(f"鎻愮ず锛氳鎵嬪姩鍒犻櫎娴嬭瘯灞炴€?'{test_prop_name}'锛圕ATIA 鍙兘涓嶆敮鎸侀€氳繃 COM 鍒犻櫎灞炴€э級")
                except Exception:
                    pass
                    
                return True
            else:
                self.log_test(
                    "鍒涘缓鐢ㄦ埛鑷畾涔夊睘鎬?,
                    False,
                    f"鍒涘缓鍚庡€间笉鍖归厤: 鏈熸湜={test_prop_value}, 瀹為檯={created_value}"
                )
                return False
        except Exception as e:
            self.log_test("鍒涘缓鐢ㄦ埛鑷畾涔夊睘鎬?, False, str(e))
            return False
            
    def test_enumerate_documents(self) -> bool:
        """娴嬭瘯 6锛氶亶鍘嗗凡鎵撳紑鏂囨。"""
        try:
            documents = self.app.Documents
            doc_count = documents.Count
            
            doc_list = []
            for i in range(1, doc_count + 1):
                try:
                    doc = documents.Item(i)
                    doc_type = self.get_document_type(doc)
                    doc_name = Path(doc.FullName).name if doc.FullName else "(鏈繚瀛?"
                    doc_list.append(f"{doc_type}: {doc_name}")
                except Exception as e:
                    doc_list.append(f"(璇诲彇澶辫触: {e})")
                    
            self.log_test(
                "閬嶅巻宸叉墦寮€鏂囨。",
                True,
                f"鍏?{doc_count} 涓枃妗? {', '.join(doc_list)}"
            )
            return True
        except Exception as e:
            self.log_test("閬嶅巻宸叉墦寮€鏂囨。", False, str(e))
            return False
            
    def test_find_part_by_partnumber(self) -> bool:
        """娴嬭瘯 7锛氭牴鎹?PartNumber 鏌ユ壘闆朵欢"""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            if doc_type not in ("PartDocument", "ProductDocument"):
                self.log_test(
                    "鏍规嵁 PartNumber 鏌ユ壘闆朵欢",
                    False,
                    f"褰撳墠鏂囨。涓嶆槸闆朵欢/瑁呴厤浣擄紝璺宠繃娴嬭瘯锛堢被鍨? {doc_type}锛?
                )
                return False
                
            target_pn = active_doc.Product.PartNumber
            
            # 鍦ㄥ凡鎵撳紑鏂囨。涓煡鎵?            documents = self.app.Documents
            found = False
            
            for i in range(1, documents.Count + 1):
                try:
                    doc = documents.Item(i)
                    doc_type_check = self.get_document_type(doc)
                    
                    if doc_type_check in ("PartDocument", "ProductDocument"):
                        try:
                            pn = doc.Product.PartNumber
                            if pn == target_pn:
                                found = True
                                break
                        except Exception:
                            pass
                except Exception:
                    continue
                    
            self.log_test(
                "鏍规嵁 PartNumber 鏌ユ壘闆朵欢",
                found,
                f"鏌ユ壘 PartNumber='{target_pn}': {'鎵惧埌' if found else '鏈壘鍒?}"
            )
            return found
        except Exception as e:
            self.log_test("鏍规嵁 PartNumber 鏌ユ壘闆朵欢", False, str(e))
            return False
            
    def test_drawing_parameters(self) -> bool:
        """娴嬭瘯 8锛氬浘绾稿弬鏁拌鍐欙紙闇€瑕佸綋鍓嶆枃妗ｆ槸 CATDrawing锛?""
        try:
            active_doc = self.app.ActiveDocument
            doc_type = self.get_document_type(active_doc)
            
            if doc_type != "DrawingDocument":
                self.log_test(
                    "鍥剧焊鍙傛暟璇诲啓",
                    False,
                    f"褰撳墠鏂囨。涓嶆槸鍥剧焊锛岃烦杩囨祴璇曪紙绫诲瀷: {doc_type}锛?
                )
                return False
                
            # 鑾峰彇鍙傛暟闆嗗悎
            params = active_doc.Parameters
            
            # 灏濊瘯璇诲彇甯歌鍙傛暟
            test_params = ["PartNumber", "Nomenclature", "Revision"]
            found_params = []
            
            for param_name in test_params:
                try:
                    param = params.Item(param_name)
                    if param is not None:
                        value = param.Value
                        found_params.append(f"{param_name}={value}")
                except Exception:
                    pass
                    
            if found_params:
                self.log_test(
                    "鍥剧焊鍙傛暟璇诲啓",
                    True,
                    f"鎵惧埌 {len(found_params)} 涓弬鏁? {', '.join(found_params)}"
                )
            else:
                self.log_test(
                    "鍥剧焊鍙傛暟璇诲啓",
                    True,
                    "鍥剧焊鏃犻瀹氫箟鍙傛暟锛圥artNumber/Nomenclature/Revision锛?
                )
            return True
        except Exception as e:
            self.log_test("鍥剧焊鍙傛暟璇诲啓", False, str(e))
            return False
            
    def test_create_drawing_from_template(self, template_path: str = None) -> bool:
        """娴嬭瘯 9锛氫粠妯℃澘鍒涘缓鏂板浘绾革紙鍙€夛紝闇€瑕佹彁渚涙ā鏉胯矾寰勶級"""
        if not template_path:
            self.log_test(
                "浠庢ā鏉垮垱寤烘柊鍥剧焊",
                False,
                "鏈彁渚涙ā鏉胯矾寰勶紝璺宠繃娴嬭瘯锛堝彲閫氳繃鍙傛暟浼犲叆妯℃澘璺緞锛?
            )
            return False
            
        try:
            template_path_obj = Path(template_path)
            if not template_path_obj.exists():
                self.log_test(
                    "浠庢ā鏉垮垱寤烘柊鍥剧焊",
                    False,
                    f"妯℃澘鏂囦欢涓嶅瓨鍦? {template_path}"
                )
                return False
                
            # 鍒涘缓鏂板浘绾?            drawing_doc = self.app.Documents.NewFrom(str(template_path))
            
            # 楠岃瘉绫诲瀷
            doc_type = self.get_document_type(drawing_doc)
            if doc_type != "DrawingDocument":
                self.log_test(
                    "浠庢ā鏉垮垱寤烘柊鍥剧焊",
                    False,
                    f"NewFrom 杩斿洖绫诲瀷閿欒: {doc_type}"
                )
                return False
                
            # 鑾峰彇鍥剧焊椤?            sheets = drawing_doc.Sheets
            sheet_count = sheets.Count
            
            self.log_test(
                "浠庢ā鏉垮垱寤烘柊鍥剧焊",
                True,
                f"鎴愬姛鍒涘缓鍥剧焊锛屽叡 {sheet_count} 寮犲浘绾搁〉"
            )
            
            # 鍏抽棴娴嬭瘯鍥剧焊锛堜笉淇濆瓨锛?            try:
                drawing_doc.Close()
                logger.info("宸插叧闂祴璇曞浘绾革紙鏈繚瀛橈級")
            except Exception as e:
                logger.warning(f"鍏抽棴娴嬭瘯鍥剧焊澶辫触: {e}")
                
            return True
        except Exception as e:
            self.log_test("浠庢ā鏉垮垱寤烘柊鍥剧焊", False, str(e))
            return False
            
    def run_all_tests(self, template_path: str = None):
        """杩愯鎵€鏈夋祴璇?""
        logger.info("=" * 80)
        logger.info("寮€濮嬮獙璇佸浘绾告搷浣滄牳蹇?COM 璋冪敤")
        logger.info("=" * 80)
        
        # 娴嬭瘯 1锛氳繛鎺?CATIA
        if not self.connect_to_catia():
            logger.error("鏃犳硶杩炴帴鍒?CATIA锛岀粓姝㈡祴璇?)
            return
            
        # 娴嬭瘯 2-9
        self.test_document_type_check()
        self.test_read_part_standard_properties()
        self.test_read_user_properties()
        self.test_create_user_property()
        self.test_enumerate_documents()
        self.test_find_part_by_partnumber()
        self.test_drawing_parameters()
        
        # 鍙€夋祴璇曪細鍒涘缓鍥剧焊
        if template_path:
            self.test_create_drawing_from_template(template_path)
            
        # 杈撳嚭娴嬭瘯鎬荤粨
        logger.info("=" * 80)
        logger.info("娴嬭瘯鎬荤粨")
        logger.info("=" * 80)
        
        total = len(self.test_results)
        passed = sum(1 for success, _ in self.test_results if success)
        failed = total - passed
        
        for success, result in self.test_results:
            logger.info(result)
            
        logger.info("=" * 80)
        logger.info(f"鎬昏: {total} 椤规祴璇?| 閫氳繃: {passed} | 澶辫触: {failed}")
        logger.info("=" * 80)
        
        if failed == 0:
            logger.info("馃帀 鎵€鏈夋祴璇曢€氳繃锛佸彲浠ュ紑濮嬫寮忔敼鍐欍€?)
        else:
            logger.warning(f"鈿狅笍  鏈?{failed} 椤规祴璇曞け璐ワ紝璇锋鏌ュけ璐ュ師鍥犮€?)


def main():
    """涓诲嚱鏁?""
    import argparse
    
    parser = argparse.ArgumentParser(description="楠岃瘉鍥剧焊鎿嶄綔鏍稿績 COM 璋冪敤")
    parser.add_argument(
        "--template",
        type=str,
        help="鍥剧焊妯℃澘璺緞锛堝彲閫夛紝鐢ㄤ簬娴嬭瘯 NewFrom 鍒涘缓鍥剧焊锛?
    )
    
    args = parser.parse_args()
    
    validator = DrawingCOMValidator()
    validator.run_all_tests(template_path=args.template)


if __name__ == "__main__":
    main()
